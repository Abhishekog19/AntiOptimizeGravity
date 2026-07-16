"""
server/ocr.py — OCR processing for Antigravity Quota Tracker

Python port of dashboard/ocr.js.
Uses Pillow for image pre-processing (dark-background inversion + upscaling)
and calls Tesseract as a subprocess — identical behaviour to the Node.js version.

Key semantic note (unchanged from the original):
  Stored pct values are % REMAINING, not % consumed.
  "90%" in the UI = 90% of quota still available. Do NOT invert before storing.
"""

from __future__ import annotations
import os
import re
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Image pre-processing ──────────────────────────────────────────────────────

def _preprocess_with_pillow(
    image_bytes: bytes,
    upscale: int,
    output_path: str,
) -> Optional[str]:
    """
    Pre-process the image using Pillow:
      1. If avg brightness < 128 (dark UI), invert colours.
      2. Upscale by `upscale` factor.
    Returns output_path on success, None if Pillow is unavailable.
    """
    try:
        from PIL import Image, ImageOps
        import io
    except ImportError:
        return None

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    # Sample brightness
    step = 20
    pixels = img.load()
    total, count = 0.0, 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = pixels[x, y]
            total += 0.299 * r + 0.587 * g + 0.114 * b
            count += 1
    avg_brightness = total / max(count, 1)

    if avg_brightness < 128:
        log.info(f"[OCR] Dark background (avg={avg_brightness:.0f}) — inverting")
        img = ImageOps.invert(img)
    else:
        log.info(f"[OCR] Light background (avg={avg_brightness:.0f}) — no inversion")

    if upscale > 1:
        img = img.resize((w * upscale, h * upscale), Image.LANCZOS)
        log.info(f"[OCR] Upscaled {upscale}x via Pillow")

    img.save(output_path)
    return output_path


# ── OCR text parser ───────────────────────────────────────────────────────────
# Mirrors parseQuotaFromText() from ocr.js — same label-map + windowed-search
# approach with identical sanity checks.

def _countdown_ms(raw: str) -> Optional[float]:
    """Total milliseconds from a multi-component countdown string."""
    if not raw:
        return None
    total = 0.0
    for amount_s, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(day|hour|minute)s?", raw, re.I):
        ms_per = {"day": 86_400_000, "hour": 3_600_000, "minute": 60_000}[unit.lower()]
        total += float(amount_s) * ms_per
    return total if total > 0 else None


def _sanity_check(label: str, raw: str, ceil_ms: float, ceil_label: str) -> None:
    ms = _countdown_ms(raw)
    if ms is not None and ms > ceil_ms:
        hrs  = ms / 3_600_000
        mins = int(ms / 60_000)
        log.warning(
            f"[OCR WARN] {label} reset parsed as {hrs:.1f}h ({mins}m) — "
            f"exceeds {ceil_label} ceiling. Raw: \"{raw}\""
        )


def _parse_quota_from_text(text: str) -> Optional[dict]:
    """Port of parseQuotaFromText() from ocr.js."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    log.debug("[OCR] Raw lines:")
    for i, l in enumerate(lines):
        log.debug(f"  [{i:2d}] {l}")

    # ── Build label map ───────────────────────────────────────────────────────
    entries = []
    for i, l in enumerate(lines):
        lo = l.lower()
        if "you have" in lo or (lines[i][0].isdigit() if lines[i] else False):
            continue  # skip countdown sentences
        if "gemini"    in lo: entries.append({"name": "gemini",   "idx": i})
        elif "claude"  in lo: entries.append({"name": "claude",   "idx": i})
        elif "weekly"  in lo: entries.append({"name": "weekly",   "idx": i})
        elif any(k in lo for k in ("five hour", "5-hour", "5 hour")):
            entries.append({"name": "fivehour", "idx": i})
    entries.sort(key=lambda e: e["idx"])

    def window_end(label_idx: int) -> int:
        nxt = next((e["idx"] for e in entries if e["idx"] > label_idx), len(lines))
        return nxt

    # ── Bounded search helpers ────────────────────────────────────────────────
    def find_pct_in(start: int, end: int) -> Optional[int]:
        if start < 0 or start >= end:
            return None
        zero_candidate = None
        for i in range(start, min(end, len(lines))):
            m = re.search(r"(\d{1,3})\s*%", lines[i])
            if m:
                v = int(m.group(1))
                if 0 < v <= 100:  return v
                if v == 0 and zero_candidate is None: zero_candidate = 0
            # Fallback: standalone integer
            is_countdown = bool(re.search(r"\b(day|hour|minute)s?\b", lines[i], re.I))
            if not is_countdown:
                m2 = re.search(r"\b(100|[1-9]\d|[1-9])\b", lines[i])
                if m2 and not re.search(r"\d{4,}", lines[i]):
                    v = int(m2.group(1))
                    if 0 < v <= 100: return v
        return zero_candidate

    def find_reset_in(start: int, end: int) -> str:
        if start < 0 or start >= end:
            return ""
        for i in range(start, min(end, len(lines))):
            if re.search(r"\d+\s*(day|hour|minute)", lines[i], re.I):
                return lines[i]
        return ""

    # ── Locate section headers ────────────────────────────────────────────────
    claude_entry = next((e for e in entries if e["name"] == "claude"), None)
    gemini_entry = next((e for e in entries if e["name"] == "gemini"), None)

    if not claude_entry:
        log.debug("[OCR] 'claude' section not found")
        return None

    claude_idx = claude_entry["idx"]
    gemini_idx = gemini_entry["idx"] if gemini_entry else -1

    # ── Claude/GPT section ────────────────────────────────────────────────────
    cw_entry = next((e for e in entries if e["name"] == "weekly"   and e["idx"] >= claude_idx), None)
    cf_entry = next((e for e in entries if e["name"] == "fivehour" and e["idx"] >= claude_idx), None)

    cw_idx = cw_entry["idx"] if cw_entry else -1
    cf_idx = cf_entry["idx"] if cf_entry else -1
    cw_end = window_end(cw_idx) if cw_entry else len(lines)
    cf_end = window_end(cf_idx) if cf_entry else len(lines)

    claude_weekly_pct     = find_pct_in(cw_idx, cw_end)
    claude_fivehour_pct   = find_pct_in(cf_idx, cf_end)
    claude_weekly_reset   = find_reset_in(cw_idx, cw_end)
    claude_fivehour_reset = find_reset_in(cf_idx, cf_end)

    _sanity_check("Claude weekly",    claude_weekly_reset,   7 * 86_400_000, "7d")
    _sanity_check("Claude five-hour", claude_fivehour_reset, 5 * 3_600_000,  "5h")

    if claude_weekly_pct is None:
        return None

    result = {
        "claudeGpt": {
            "weeklyPct":        claude_weekly_pct,
            "fiveHourPct":      claude_fivehour_pct if claude_fivehour_pct is not None else 0,
            "weeklyResetRaw":   claude_weekly_reset,
            "fiveHourResetRaw": claude_fivehour_reset,
        }
    }

    # ── Gemini section ────────────────────────────────────────────────────────
    if gemini_idx >= 0 and gemini_idx < claude_idx:
        gw_entry = next((e for e in entries if e["name"] == "weekly"   and gemini_idx <= e["idx"] < claude_idx), None)
        gf_entry = next((e for e in entries if e["name"] == "fivehour" and gemini_idx <= e["idx"] < claude_idx), None)

        gw_idx = gw_entry["idx"] if gw_entry else -1
        gf_idx = gf_entry["idx"] if gf_entry else -1
        gw_end = min(window_end(gw_idx) if gw_entry else claude_idx, claude_idx)
        gf_end = min(window_end(gf_idx) if gf_entry else claude_idx, claude_idx)

        gemini_weekly_pct   = find_pct_in(gw_idx, gw_end)
        gemini_fivehour_pct = find_pct_in(gf_idx, gf_end)
        gemini_weekly_reset   = find_reset_in(gw_idx, gw_end)
        gemini_fivehour_reset = find_reset_in(gf_idx, gf_end)

        _sanity_check("Gemini weekly",    gemini_weekly_reset,   7 * 86_400_000, "7d")
        _sanity_check("Gemini five-hour", gemini_fivehour_reset, 5 * 3_600_000,  "5h")

        if gemini_weekly_pct is not None:
            result["gemini"] = {
                "weeklyPct":        gemini_weekly_pct,
                "fiveHourPct":      gemini_fivehour_pct if gemini_fivehour_pct is not None else 0,
                "weeklyResetRaw":   gemini_weekly_reset,
                "fiveHourResetRaw": gemini_fivehour_reset,
            }

    log.debug(f"[OCR] Parsed: {result}")
    return result


# ── Tesseract runner ──────────────────────────────────────────────────────────

def tesseract_available(tesseract_path: str = "tesseract") -> bool:
    """Return True if Tesseract binary is reachable."""
    try:
        subprocess.run(
            [tesseract_path, "--version"],
            capture_output=True, timeout=5
        )
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return True  # binary found, just errored — still present


def ocr_image(
    image_bytes: bytes,
    mime_type: str,
    tesseract_path: str = "tesseract",
    psm: int = 3,
    upscale: int = 2,
) -> dict:
    """
    Run Tesseract OCR on image_bytes and return parsed quota data.

    Returns: { text: str, quota: dict | None }
    Mirrors ocrImage() from ocr.js including PSM auto-retry.
    """
    ext = ".png" if "png" in mime_type else ".jpg"

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, f"ocr_in{ext}")
        pre_path = os.path.join(tmp_dir, f"ocr_pre{ext}")
        out_base = os.path.join(tmp_dir, "ocr_out")
        out_txt  = out_base + ".txt"

        # Write raw input
        with open(raw_path, "wb") as f:
            f.write(image_bytes)

        # Pre-process
        ocr_input = raw_path
        processed = _preprocess_with_pillow(image_bytes, upscale, pre_path)
        if processed:
            ocr_input = processed
        else:
            # Fallback: ImageMagick
            try:
                subprocess.run(
                    ["convert", raw_path, "-resize", f"{upscale * 100}%", pre_path],
                    capture_output=True, timeout=10
                )
                ocr_input = pre_path
                log.info("[OCR] Upscaled via ImageMagick")
            except Exception:
                log.warning("[OCR] No preprocessing available — using raw image")

        def run_tesseract(psm_mode: int) -> dict:
            subprocess.run(
                [tesseract_path, ocr_input, out_base, "--psm", str(psm_mode), "-l", "eng"],
                capture_output=True, timeout=25, check=True
            )
            text = Path(out_txt).read_text(encoding="utf-8", errors="replace")
            log.debug(f"[OCR] Tesseract PSM={psm_mode} output:\n{text}")
            quota = _parse_quota_from_text(text)
            return {"text": text, "quota": quota, "psm": psm_mode}

        result = run_tesseract(psm)

        if not result["quota"]:
            alt_psm = 6 if psm == 3 else 3
            log.info(f"[OCR] PSM={psm} failed — retrying with PSM={alt_psm}")
            retry = run_tesseract(alt_psm)
            if retry["quota"]:
                log.info(f"[OCR] PSM={alt_psm} retry succeeded")
                result = retry
            else:
                log.info(f"[OCR] Both PSM={psm} and PSM={alt_psm} failed to parse quota")

        return {"text": result["text"], "quota": result["quota"]}
