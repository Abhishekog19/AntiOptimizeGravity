const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

/**
 * Parses quota percentages from OCR text extracted from a Settings → Models screenshot.
 * Works on the raw text output from Tesseract.
 */
function parseQuotaFromText(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);

  // Find line index of a keyword
  function findLine(keyword) {
    return lines.findIndex(l => l.toLowerCase().includes(keyword.toLowerCase()));
  }

  // Find a percentage within N lines after a starting line index
  function findPctAfter(startIdx, range = 6) {
    if (startIdx < 0) return null;
    for (let i = startIdx; i < Math.min(startIdx + range, lines.length); i++) {
      const m = lines[i].match(/\b(\d{1,3})\s*%/);
      if (m) {
        const v = parseInt(m[1]);
        if (v >= 0 && v <= 100) return v;
      }
      // Also catch standalone numbers when OCR drops the % symbol.
      // Skip lines containing countdown words ("day", "hour", "minute") to avoid
      // falsely matching numbers like "4" in "fully refresh in 4 days, 19 hours".
      const isCountdownLine = /\b(day|hour|minute)s?\b/i.test(lines[i]);
      const m2 = !isCountdownLine && lines[i].match(/\b(100|[1-9]\d|[1-9])\b/);
      if (m2 && !lines[i].match(/\d{4,}/)) {
        const v = parseInt(m2[1]);
        if (v >= 0 && v <= 100) return v;
      }
    }
    return null;
  }

  // Find reset countdown text within N lines after a starting line index
  function findResetAfter(startIdx, range = 8) {
    if (startIdx < 0) return "";
    for (let i = startIdx; i < Math.min(startIdx + range, lines.length); i++) {
      if (/\d+\s*(day|hour|minute)/i.test(lines[i])) return lines[i];
    }
    return "";
  }

  // Locate section headers
  const geminiIdx = findLine("gemini");
  const claudeIdx = findLine("claude");

  if (claudeIdx < 0) return null;

  // Claude/GPT section: find Weekly and Five Hour within it
  const claudeWeeklyIdx = lines.findIndex((l, i) =>
    i >= claudeIdx && l.toLowerCase().includes("weekly")
  );
  const claudeFiveHourIdx = lines.findIndex((l, i) =>
    i >= claudeIdx &&
    (l.toLowerCase().includes("five hour") ||
      l.toLowerCase().includes("5-hour") ||
      l.toLowerCase().includes("5 hour"))
  );

  const claudeWeeklyPct = findPctAfter(claudeWeeklyIdx);
  const claudeFiveHourPct = findPctAfter(claudeFiveHourIdx);
  const claudeReset = findResetAfter(claudeWeeklyIdx);

  if (claudeWeeklyPct === null) return null;

  const result = {
    claudeGpt: {
      weeklyPct: claudeWeeklyPct,
      fiveHourPct: claudeFiveHourPct ?? 0,
      resetCountdownRaw: claudeReset,
    },
  };

  // Gemini section (must come before claudeIdx in the text)
  if (geminiIdx >= 0) {
    const geminiWeeklyIdx = lines.findIndex((l, i) =>
      i >= geminiIdx && i < claudeIdx && l.toLowerCase().includes("weekly")
    );
    const geminiFiveHourIdx = lines.findIndex((l, i) =>
      i >= geminiIdx &&
      i < claudeIdx &&
      (l.toLowerCase().includes("five hour") || l.toLowerCase().includes("5-hour"))
    );
    const geminiWeeklyPct = findPctAfter(geminiWeeklyIdx);
    const geminiFiveHourPct = findPctAfter(geminiFiveHourIdx);
    const geminiReset = findResetAfter(geminiWeeklyIdx);

    if (geminiWeeklyPct !== null) {
      result.gemini = {
        weeklyPct: geminiWeeklyPct,
        fiveHourPct: geminiFiveHourPct ?? 0,
        resetCountdownRaw: geminiReset,
      };
    }
  }

  return result;
}

/**
 * Runs Tesseract OCR on an image buffer and returns parsed quota data.
 *
 * @param {Buffer} imageBuffer   - Raw image bytes
 * @param {string} mimeType      - MIME type (e.g. "image/png")
 * @param {string} tesseractPath - Full path to tesseract binary (or just "tesseract" if on PATH)
 * @param {string|number} psm    - Tesseract page-segmentation mode (default "3" = auto)
 * @param {number} upscale       - Integer upscale factor before OCR (default 2 = 2×)
 */
function ocrImage(imageBuffer, mimeType, tesseractPath = "tesseract", psm = 3, upscale = 2) {
  const ext = mimeType.includes("png") ? ".png" : ".jpg";
  const tmpIn = path.join(os.tmpdir(), `quota_ocr_in${ext}`);
  const tmpOut = path.join(os.tmpdir(), "quota_ocr_out");

  fs.writeFileSync(tmpIn, imageBuffer);

  // ── Optional upscaling via ImageMagick `convert` (if available) ───────────
  // If ImageMagick is not installed this step is skipped gracefully; Tesseract
  // still runs on the original image. The user can install ImageMagick to
  // improve OCR accuracy on small UI text.
  let ocrInput = tmpIn;
  if (upscale && upscale > 1) {
    const tmpScaled = path.join(os.tmpdir(), `quota_ocr_scaled${ext}`);
    try {
      execSync(
        `convert "${tmpIn}" -resize ${upscale * 100}% "${tmpScaled}"`,
        { timeout: 10000, stdio: "pipe" }
      );
      ocrInput = tmpScaled;
    } catch {
      // ImageMagick not available or failed — fall through to unscaled image
      console.warn("[OCR] ImageMagick upscaling skipped (not installed or failed).");
    }
  }

  try {
    execSync(
      `"${tesseractPath}" "${ocrInput}" "${tmpOut}" --psm ${psm} -l eng`,
      { timeout: 20000, stdio: "pipe" }
    );

    const text = fs.readFileSync(`${tmpOut}.txt`, "utf8");
    const quota = parseQuotaFromText(text);
    return { text, quota };
  } finally {
    // Cleanup all temp files
    for (const f of [tmpIn, ocrInput, `${tmpOut}.txt`]) {
      try { fs.unlinkSync(f); } catch { /* ignore */ }
    }
  }
}

module.exports = { ocrImage, parseQuotaFromText };
