#!/usr/bin/env python3
"""
Add month-wise nutrition labels to cleaned_dataset.csv.

Uses src/nutrition_labels.py lookup logic and writes a new CSV with columns:
- {month}_stunting_status, {month}_is_stunted
- {month}_underweight_status, {month}_is_underweight
- {month}_wasting_status, {month}_is_wasted, {month}_is_sam

Default input:  data/cleaned_dataset.csv
Default output: data/cleaned_dataset_with_labels.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from src.nutrition_labels import classify_all  # noqa: E402


MONTH_CONFIG = {
    "feb24": pd.Timestamp("2024-02-01"),
    "mar24": pd.Timestamp("2024-03-01"),
    "apr24": pd.Timestamp("2024-04-01"),
}


def _normalize_sex(value) -> str:
    if pd.isna(value):
        return "M"
    v = str(value).strip().upper()
    if v in {"M", "MALE", "BOY"}:
        return "M"
    if v in {"F", "FEMALE", "GIRL"}:
        return "F"
    return "M"


def _safe_positive_float(value) -> float | None:
    if pd.isna(value):
        return None
    try:
        n = float(value)
    except Exception:
        return None
    return n if n > 0 else None


def _default_label_dict() -> dict:
    return {
        "stunting_status": None,
        "is_stunted": None,
        "underweight_status": None,
        "is_underweight": None,
        "wasting_status": None,
        "is_wasted": None,
        "is_sam": None,
    }


def _drop_zero_measurement_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop rows where any height/weight measurement is exactly zero."""
    measurement_cols = []
    for month in MONTH_CONFIG:
        for metric in ("height", "weight"):
            col = f"{month}_{metric}"
            if col in df.columns:
                measurement_cols.append(col)

    if not measurement_cols:
        return df, 0

    zero_mask = pd.Series(False, index=df.index)
    for col in measurement_cols:
        numeric_col = pd.to_numeric(df[col], errors="coerce")
        zero_mask = zero_mask | (numeric_col == 0)

    dropped_count = int(zero_mask.sum())
    if dropped_count == 0:
        return df, 0

    return df.loc[~zero_mask].copy(), dropped_count


def _label_month(df: pd.DataFrame, month: str) -> list[dict]:
    h_col = f"{month}_height"
    w_col = f"{month}_weight"
    d_col = f"{month}_height_weight_entered_date"

    dob_dt = pd.to_datetime(df["dob"], errors="coerce")
    entered_dt = pd.to_datetime(df[d_col], errors="coerce")
    measured_dt = entered_dt.fillna(MONTH_CONFIG[month])
    age_days_series = (measured_dt - dob_dt).dt.days

    total = len(df)
    labels: list[dict] = []

    iterator = zip(
        df["gender"],
        age_days_series,
        df[h_col],
        df[w_col],
    )

    for gender, age_days_value, height_value, weight_value in tqdm(
        iterator,
        total=total,
        desc=f"Labeling {month}",
        unit="rows",
        dynamic_ncols=True,
    ):
        if pd.isna(age_days_value):
            labels.append(_default_label_dict())
            continue

        age_days = int(max(age_days_value, 0))
        sex = _normalize_sex(gender)
        height_cm = _safe_positive_float(height_value)
        weight_kg = _safe_positive_float(weight_value)

        labels.append(
            classify_all(
                sex=sex,
                age_days=age_days,
                height_cm=height_cm,
                weight_kg=weight_kg,
            )
        )

    return labels


def _apply_month_labels(df: pd.DataFrame, month: str) -> None:
    labels = _label_month(df, month)

    df[f"{month}_stunting_status"] = [x["stunting_status"] for x in labels]
    df[f"{month}_is_stunted"] = [x["is_stunted"] for x in labels]
    df[f"{month}_underweight_status"] = [x["underweight_status"] for x in labels]
    df[f"{month}_is_underweight"] = [x["is_underweight"] for x in labels]
    df[f"{month}_wasting_status"] = [x["wasting_status"] for x in labels]
    df[f"{month}_is_wasted"] = [x["is_wasted"] for x in labels]
    df[f"{month}_is_sam"] = [x["is_sam"] for x in labels]


def add_labels(input_csv: Path, output_csv: Path, chunksize: int = 50_000) -> None:
    print(f"Reading input CSV in chunks: {input_csv}")
    print(f"Chunk size: {chunksize:,}")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    wrote_header = False
    total_dropped = 0

    chunk_iter = pd.read_csv(input_csv, low_memory=False, chunksize=chunksize)
    for chunk_idx, df in enumerate(chunk_iter, start=1):
        original_chunk_rows = len(df)
        df, dropped_count = _drop_zero_measurement_rows(df)
        total_dropped += dropped_count

        print(
            f"Processing chunk {chunk_idx} with {original_chunk_rows:,} rows "
            f"({dropped_count:,} dropped, {len(df):,} kept)"
        )

        if df.empty:
            print(f"Skipped chunk {chunk_idx}: no rows left after zero-measurement filtering")
            continue

        for month in MONTH_CONFIG:
            _apply_month_labels(df, month)

        mode = "w" if not wrote_header else "a"
        df.to_csv(output_csv, index=False, mode=mode, header=not wrote_header)
        wrote_header = True
        print(f"Wrote chunk {chunk_idx}")

    print(f"Total rows dropped (zero height/weight): {total_dropped:,}")
    print(f"Saved labeled CSV: {output_csv}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Add nutrition labels to cleaned CSV")
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "data" / "cleaned_dataset.csv"),
        help="Path to input cleaned CSV",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "cleaned_dataset_with_labels.csv"),
        help="Path to output labeled CSV",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=50_000,
        help="Rows per chunk for streaming read/write (lower to reduce memory)",
    )
    args = parser.parse_args()

    input_csv = Path(args.input)
    output_csv = Path(args.output)

    if not input_csv.exists():
        print(f"Error: input CSV not found: {input_csv}")
        return 1

    if args.chunksize <= 0:
        print("Error: --chunksize must be > 0")
        return 1

    add_labels(input_csv, output_csv, chunksize=args.chunksize)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
