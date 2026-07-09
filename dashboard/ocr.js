const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

// ── Image pre-processing via jimp (pure JS, no native deps) ──────────────────
//
// SEMANTIC NOTE: stored pct values are % REMAINING, not % consumed.
// The Antigravity UI shows "90%" on a Weekly Limit that just started ~1 h ago,
// meaning 90% of quota is still available.  Do NOT invert before storing.

/**
 * Pre-process an image buffer for OCR:
 *  1. If the image is predominantly dark (dark-theme UI), invert colours so
 *     Tesseract sees dark text on a light background (much more reliable).
 *  2. Upscale by `upscale` factor for small-text accuracy.
 *
 * Returns the output file path on success, or null if jimp is unavailable.
 */
async function preprocessWithJimp(buffer, mimeType, outputPath, upscale) {
  let Jimp;
  try {
    Jimp = require("jimp");
  } catch {
    return null; // jimp not installed — caller will fall back to ImageMagick
  }

  const image = await Jimp.read(buffer);
  const { width, height, data } = image.bitmap;

  // ── Brightness sampling to detect dark backgrounds ──────────────────────────
  const STEP = 20;
  let total = 0, count = 0;
  for (let y = 0; y < height; y += STEP) {
    for (let x = 0; x < width; x += STEP) {
      const idx = (y * width + x) * 4;
      total += 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      count++;
    }
  }
  const avgBrightness = total / count;

  if (avgBrightness < 128) {
    console.log(`[OCR] Dark background detected (avg brightness ${avgBrightness.toFixed(0)}) — inverting colours for OCR`);
    image.invert();
  } else {
    console.log(`[OCR] Light background (avg brightness ${avgBrightness.toFixed(0)}) — no inversion needed`);
  }

  if (upscale > 1) {
    image.resize(width * upscale, height * upscale);
    console.log(`[OCR] Upscaled image ${upscale}x via jimp`);
  }

  await image.writeAsync(outputPath);
  return outputPath;
}

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parses quota percentages + reset countdowns from Tesseract OCR text.
 *
 * SEMANTIC NOTE: the Antigravity UI shows **% REMAINING** capacity.
 * Example: "90% · fully refresh in 6 days, 23 hours" = 90% still available.
 *
 * KEY INVARIANT: every search (% value and reset text) is strictly bounded to
 * [thisLabelLine, nextLabelLine) — it can NEVER bleed into an adjacent row.
 */
function parseQuotaFromText(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);

  // Always log raw lines — essential for diagnosing OCR output ordering
  console.log("[OCR DEBUG] Raw lines from Tesseract:");
  lines.forEach((l, i) => console.log(`  [${String(i).padStart(2)}] ${l}`));

  // ── Label map ──────────────────────────────────────────────────────────────

  /**
   * Build a sorted array of { name, idx } for every known section/row label
   * found in the OCR output.
   *
   * Labels recognised (case-insensitive):
   *   "gemini"   → Gemini Models section header
   *   "claude"   → Claude and GPT models section header
   *   "weekly"   → Weekly Limit row heading
   *   "fivehour" → Five Hour Limit row heading (three spelling variants)
   *
   * Countdown sentences ("You have used some of your…") are excluded so they
   * don't accidentally get registered as label lines.
   */
  function buildLabelMap() {
    const entries = [];
    lines.forEach((l, i) => {
      const lo = l.toLowerCase();
      if (lo.includes("you have") || /^\d/.test(l)) return; // skip countdown sentences

      if (lo.includes("gemini"))                                                       entries.push({ name: "gemini",   idx: i });
      else if (lo.includes("claude"))                                                  entries.push({ name: "claude",   idx: i });
      else if (lo.includes("weekly"))                                                  entries.push({ name: "weekly",   idx: i });
      else if (lo.includes("five hour") || lo.includes("5-hour") || lo.includes("5 hour")) entries.push({ name: "fivehour", idx: i });
    });
    entries.sort((a, b) => a.idx - b.idx);
    return entries;
  }

  /**
   * Return the exclusive upper bound for searches starting at `labelIdx`.
   * That is: the line-index of the NEXT label in sorted order, or
   * lines.length if there is none.
   */
  function windowEnd(labelIdx, entries) {
    const next = entries.find(e => e.idx > labelIdx);
    return next ? next.idx : lines.length;
  }

  // ── Bounded search helpers ─────────────────────────────────────────────────

  /**
   * Find a percentage value strictly within [start, end).
   * Prefers values > 0; only returns 0 if nothing better is found.
   * Countdown lines are skipped for the standalone-number fallback to avoid
   * picking up e.g. "4" from "4 days, 19 hours".
   */
  function findPctIn(start, end) {
    if (start < 0 || start >= end) return null;
    const cap = Math.min(end, lines.length);
    let zeroCandidate = null;
    for (let i = start; i < cap; i++) {
      // Primary: explicit "NN%" pattern (handles "100%", "78 %", etc.)
      const m = lines[i].match(/(\d{1,3})\s*%/);
      if (m) {
        const v = parseInt(m[1]);
        if (v > 0 && v <= 100) return v;
        if (v === 0 && zeroCandidate === null) zeroCandidate = 0;
      }
      // Fallback: standalone integer 1–100 (OCR sometimes drops the "%" glyph)
      const isCountdownLine = /\b(day|hour|minute)s?\b/i.test(lines[i]);
      if (!isCountdownLine) {
        const m2 = lines[i].match(/\b(100|[1-9]\d|[1-9])\b/);
        if (m2 && !lines[i].match(/\d{4,}/)) {
          const v = parseInt(m2[1]);
          if (v > 0 && v <= 100) return v;
        }
      }
    }
    return zeroCandidate;
  }

  /**
   * Find the first reset-countdown sentence strictly within [start, end).
   * A countdown sentence contains a digit followed by day/hour/minute.
   */
  function findResetIn(start, end) {
    if (start < 0 || start >= end) return "";
    const cap = Math.min(end, lines.length);
    for (let i = start; i < cap; i++) {
      if (/\d+\s*(day|hour|minute)/i.test(lines[i])) return lines[i];
    }
    return "";
  }

  // ── Sanity checks ──────────────────────────────────────────────────────────

  /** Compute total ms from a multi-component countdown string. */
  function countdownMs(raw) {
    if (!raw) return null;
    let ms = 0;
    const re = /(\d+(?:\.\d+)?)\s*(day|hour|minute)s?/gi;
    let m;
    while ((m = re.exec(raw)) !== null) {
      const unit = { day: 86400000, hour: 3600000, minute: 60000 }[m[2].toLowerCase()];
      ms += parseFloat(m[1]) * unit;
    }
    return ms > 0 ? ms : null;
  }

  function sanityCheck(label, raw, ceilMs, ceilLabel) {
    const ms = countdownMs(raw);
    if (ms !== null && ms > ceilMs) {
      const hrs = (ms / 3600000).toFixed(1);
      const mins = Math.round(ms / 60000);
      console.warn(
        `[OCR WARN] ${label} reset parsed as ${hrs}h (${mins}m) — exceeds ${ceilLabel} ceiling, likely mis-parsed. Raw: "${raw}"`
      );
    }
  }

  // ── Build and log label map ────────────────────────────────────────────────

  const labelEntries = buildLabelMap();

  console.log("[OCR DEBUG] Label index map:");
  labelEntries.forEach(e =>
    console.log(`  ${e.name.padEnd(10)} @ line ${String(e.idx).padStart(2)}: "${lines[e.idx]}"`)
  );

  console.log("[OCR DEBUG] Computed search windows [start, end):");
  labelEntries.forEach(e => {
    const end = windowEnd(e.idx, labelEntries);
    const preview = lines.slice(e.idx, Math.min(end, e.idx + 5))
      .map((l, off) => `[${e.idx + off}]"${l.slice(0, 55)}"`)
      .join(", ");
    console.log(`  ${e.name.padEnd(10)} [${e.idx}, ${end}) → ${preview}`);
  });

  // ── Locate top-level section headers ──────────────────────────────────────

  const claudeEntry = labelEntries.find(e => e.name === "claude");
  const geminiEntry = labelEntries.find(e => e.name === "gemini");

  if (!claudeEntry) {
    console.log("[OCR DEBUG] 'claude' section not found — returning null");
    return null;
  }

  const claudeIdx = claudeEntry.idx;
  const geminiIdx = geminiEntry ? geminiEntry.idx : -1;

  // ── Claude/GPT section ─────────────────────────────────────────────────────
  // Row labels must come AFTER the Claude section header.
  const claudeWeeklyEntry   = labelEntries.find(e => e.name === "weekly"   && e.idx >= claudeIdx);
  const claudeFiveHourEntry = labelEntries.find(e => e.name === "fivehour" && e.idx >= claudeIdx);

  const claudeWeeklyIdx   = claudeWeeklyEntry   ? claudeWeeklyEntry.idx   : -1;
  const claudeFiveHourIdx = claudeFiveHourEntry ? claudeFiveHourEntry.idx : -1;

  // Each window ends strictly at the next label — never bleeds over.
  const claudeWeeklyEnd   = claudeWeeklyEntry   ? windowEnd(claudeWeeklyIdx,   labelEntries) : lines.length;
  const claudeFiveHourEnd = claudeFiveHourEntry ? windowEnd(claudeFiveHourIdx, labelEntries) : lines.length;

  console.log(`[OCR DEBUG] claudeWeekly   window=[${claudeWeeklyIdx},   ${claudeWeeklyEnd})`);
  console.log(`[OCR DEBUG] claudeFiveHour window=[${claudeFiveHourIdx}, ${claudeFiveHourEnd})`);

  const claudeWeeklyPct     = findPctIn(claudeWeeklyIdx,   claudeWeeklyEnd);
  const claudeFiveHourPct   = findPctIn(claudeFiveHourIdx, claudeFiveHourEnd);
  const claudeWeeklyReset   = findResetIn(claudeWeeklyIdx,   claudeWeeklyEnd);
  const claudeFiveHourReset = findResetIn(claudeFiveHourIdx, claudeFiveHourEnd);

  console.log(`[OCR DEBUG] claudeWeeklyPct=${claudeWeeklyPct}  claudeFiveHourPct=${claudeFiveHourPct}`);
  console.log(`[OCR DEBUG] claudeWeeklyReset="${claudeWeeklyReset}"`);
  console.log(`[OCR DEBUG] claudeFiveHourReset="${claudeFiveHourReset}"`);

  sanityCheck("Claude weekly",    claudeWeeklyReset,   7 * 86400000, "7d");
  sanityCheck("Claude five-hour", claudeFiveHourReset, 5 * 3600000,  "5h");

  if (claudeWeeklyPct === null) {
    console.log("[OCR DEBUG] claudeWeeklyPct=null — returning null quota");
    return null;
  }

  const result = {
    claudeGpt: {
      weeklyPct:        claudeWeeklyPct,
      fiveHourPct:      claudeFiveHourPct ?? 0,
      weeklyResetRaw:   claudeWeeklyReset,
      fiveHourResetRaw: claudeFiveHourReset,
    },
  };

  // ── Gemini section ─────────────────────────────────────────────────────────
  // Gemini must appear BEFORE the Claude header in the OCR output.
  if (geminiIdx >= 0 && geminiIdx < claudeIdx) {
    // Row labels must be between geminiIdx and claudeIdx (exclusive).
    const geminiWeeklyEntry   = labelEntries.find(e => e.name === "weekly"   && e.idx >= geminiIdx && e.idx < claudeIdx);
    const geminiFiveHourEntry = labelEntries.find(e => e.name === "fivehour" && e.idx >= geminiIdx && e.idx < claudeIdx);

    const geminiWeeklyIdx   = geminiWeeklyEntry   ? geminiWeeklyEntry.idx   : -1;
    const geminiFiveHourIdx = geminiFiveHourEntry ? geminiFiveHourEntry.idx : -1;

    // Cap all Gemini windows at claudeIdx so they cannot bleed into the Claude section.
    const geminiWeeklyEnd   = Math.min(
      geminiWeeklyEntry   ? windowEnd(geminiWeeklyIdx,   labelEntries) : claudeIdx,
      claudeIdx
    );
    const geminiFiveHourEnd = Math.min(
      geminiFiveHourEntry ? windowEnd(geminiFiveHourIdx, labelEntries) : claudeIdx,
      claudeIdx
    );

    console.log(`[OCR DEBUG] geminiWeekly   window=[${geminiWeeklyIdx},   ${geminiWeeklyEnd})`);
    console.log(`[OCR DEBUG] geminiFiveHour window=[${geminiFiveHourIdx}, ${geminiFiveHourEnd})`);

    const geminiWeeklyPct   = findPctIn(geminiWeeklyIdx,   geminiWeeklyEnd);
    const geminiFiveHourPct = findPctIn(geminiFiveHourIdx, geminiFiveHourEnd);
    const geminiWeeklyReset   = findResetIn(geminiWeeklyIdx,   geminiWeeklyEnd);
    const geminiFiveHourReset = findResetIn(geminiFiveHourIdx, geminiFiveHourEnd);

    console.log(`[OCR DEBUG] geminiWeeklyPct=${geminiWeeklyPct}  geminiFiveHourPct=${geminiFiveHourPct}`);
    console.log(`[OCR DEBUG] geminiWeeklyReset="${geminiWeeklyReset}"`);
    console.log(`[OCR DEBUG] geminiFiveHourReset="${geminiFiveHourReset}"`);

    sanityCheck("Gemini weekly",    geminiWeeklyReset,   7 * 86400000, "7d");
    sanityCheck("Gemini five-hour", geminiFiveHourReset, 5 * 3600000,  "5h");

    if (geminiWeeklyPct !== null) {
      result.gemini = {
        weeklyPct:        geminiWeeklyPct,
        fiveHourPct:      geminiFiveHourPct ?? 0,
        weeklyResetRaw:   geminiWeeklyReset,
        fiveHourResetRaw: geminiFiveHourReset,
      };
    }
  }

  // ── Final debug summary (printed immediately before the API response) ──────
  console.log("[OCR DEBUG] === Final parsed result ===");
  console.log(JSON.stringify(result, null, 2));

  return result;
}

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Runs Tesseract OCR on an image buffer and returns parsed quota data.
 * ASYNC so jimp preprocessing (invert + upscale) can be awaited.
 * Automatically retries with the alternate PSM if the primary produces no parse.
 */
async function ocrImage(imageBuffer, mimeType, tesseractPath = "tesseract", psm = 6, upscale = 2) {
  const ext    = mimeType.includes("png") ? ".png" : ".jpg";
  const tmpIn  = path.join(os.tmpdir(), `quota_ocr_in${ext}`);
  const tmpPre = path.join(os.tmpdir(), `quota_ocr_pre${ext}`);
  const tmpOut = path.join(os.tmpdir(), "quota_ocr_out");

  fs.writeFileSync(tmpIn, imageBuffer);

  // ── Pre-processing: jimp → ImageMagick → raw (in order of preference) ─────
  let ocrInput = tmpIn;

  const preprocessed = await preprocessWithJimp(imageBuffer, mimeType, tmpPre, upscale);
  if (preprocessed) {
    ocrInput = preprocessed;
  } else {
    try {
      execSync(`convert "${tmpIn}" -resize ${upscale * 100}% "${tmpPre}"`, { timeout: 10000, stdio: "pipe" });
      ocrInput = tmpPre;
      console.log("[OCR] Upscaled via ImageMagick");
    } catch {
      console.warn("[OCR] No preprocessing available (no jimp, no ImageMagick) — using raw image");
    }
  }

  function runTesseract(psmMode) {
    execSync(
      `"${tesseractPath}" "${ocrInput}" "${tmpOut}" --psm ${psmMode} -l eng`,
      { timeout: 25000, stdio: "pipe" }
    );
    const text = fs.readFileSync(`${tmpOut}.txt`, "utf8");
    console.log(`\n[OCR] === Tesseract output (PSM ${psmMode}) ===\n${text}`);
    const quota = parseQuotaFromText(text);
    return { text, quota, psm: psmMode };
  }

  try {
    let result = runTesseract(psm);

    // Automatic PSM retry: PSM 3 ↔ PSM 6
    if (!result.quota) {
      const altPsm = String(psm) === "3" ? 6 : 3;
      console.log(`[OCR] Primary PSM=${psm} produced no quota — retrying with PSM=${altPsm}`);
      const retry = runTesseract(altPsm);
      if (retry.quota) {
        console.log(`[OCR] PSM=${altPsm} retry succeeded`);
        result = retry;
      } else {
        console.log(`[OCR] Both PSM=${psm} and PSM=${altPsm} failed to parse quota`);
      }
    }

    return { text: result.text, quota: result.quota };
  } finally {
    for (const f of [tmpIn, tmpPre, `${tmpOut}.txt`]) {
      try { fs.unlinkSync(f); } catch { /* ignore */ }
    }
  }
}

module.exports = { ocrImage, parseQuotaFromText };
