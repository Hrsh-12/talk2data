#!/usr/bin/env python3
"""
Parse the three assessment PDFs into CSV lookup tables.

- Stunting & Underweight: key = (sex, day) where day = age in days.
- Wasting: key = (sex, age_band, height_cm).

Output: data/processed/stunting_lookup.csv, underweight_lookup.csv, wasting_lookup.csv
"""

import csv
import re
from pathlib import Path

from PyPDF2 import PdfReader

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROCESSED_DIR = DATA_DIR / "processed"


def _extract_all_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(
        (p.extract_text() or "") for p in reader.pages
    )


def parse_stunting_pdf(pdf_path: Path) -> list[dict]:
    """Parse StuntedAssessmentParameters.pdf -> list of (sex, day, h_severe_max, h_normal_min)."""
    text = _extract_all_text(pdf_path)
    rows = []
    pattern = re.compile(
        r"^(Girls|Boys)\s+(\d+-\d+)\s+years\s+(\d+)\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        sex = "F" if m.group(1).lower() == "girls" else "M"
        rows.append({
            "sex": sex,
            "age_group": m.group(2),
            "day": int(m.group(3)),
            "h_severe_max": float(m.group(4)),
            "h_normal_min": float(m.group(5)),
        })
    return rows


def parse_underweight_pdf(pdf_path: Path) -> list[dict]:
    """Parse UnderweightAssessmentParameters.pdf -> list of (sex, day, w_severe_max, w_normal_min)."""
    text = _extract_all_text(pdf_path)
    rows = []
    pattern = re.compile(
        r"^(Girls|Boys)\s+(\d+-\d+)\s+years\s+(\d+)\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        sex = "F" if m.group(1).lower() == "girls" else "M"
        rows.append({
            "sex": sex,
            "age_group": m.group(2),
            "day": int(m.group(3)),
            "w_severe_max": float(m.group(4)),
            "w_normal_min": float(m.group(5)),
        })
    return rows


def parse_wasting_pdf(pdf_path: Path) -> list[dict]:
    """Parse WastedAssessmentParameters.pdf -> (sex, age_band, height_cm, thresholds)."""
    text = _extract_all_text(pdf_path)
    rows = []
    # "Girl 0-2 yrs 45 1.902 1.902 2.066 2.066 2.967 2.967 3.275 3.275"
    pattern = re.compile(
        r"^(Girl|Boy)\s+(\d+-\d+)\s+yrs\s+([\d.]+)\s+"
        r"([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        sex = "F" if m.group(1).lower() == "girl" else "M"
        rows.append({
            "sex": sex,
            "age_band": m.group(2),
            "height_cm": float(m.group(3)),
            "sam_upper": float(m.group(4)),
            "mam_upper": float(m.group(5)),
            "normal_upper": float(m.group(6)),
            "overweight_upper": float(m.group(7)),
        })
    return rows


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    stunting_pdf = DATA_DIR / "StuntedAssessmentParameters.pdf"
    if stunting_pdf.exists():
        rows = parse_stunting_pdf(stunting_pdf)
        out = PROCESSED_DIR / "stunting_lookup.csv"
        _write_csv(out, rows, ["sex", "age_group", "day", "h_severe_max", "h_normal_min"])
        print(f"Stunting: {len(rows)} rows -> {out}")
    else:
        print("Not found:", stunting_pdf)

    underweight_pdf = DATA_DIR / "UnderweightAssessmentParameters.pdf"
    if underweight_pdf.exists():
        rows = parse_underweight_pdf(underweight_pdf)
        out = PROCESSED_DIR / "underweight_lookup.csv"
        _write_csv(out, rows, ["sex", "age_group", "day", "w_severe_max", "w_normal_min"])
        print(f"Underweight: {len(rows)} rows -> {out}")
    else:
        print("Not found:", underweight_pdf)

    wasting_pdf = DATA_DIR / "WastedAssessmentParameters.pdf"
    if wasting_pdf.exists():
        rows = parse_wasting_pdf(wasting_pdf)
        out = PROCESSED_DIR / "wasting_lookup.csv"
        _write_csv(out, rows, ["sex", "age_band", "height_cm", "sam_upper", "mam_upper", "normal_upper", "overweight_upper"])
        print(f"Wasting: {len(rows)} rows -> {out}")
    else:
        print("Not found:", wasting_pdf)


if __name__ == "__main__":
    main()
