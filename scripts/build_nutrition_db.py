#!/usr/bin/env python3
"""
Build a persistent DuckDB from the labeled nutrition CSV.

Usage:
  python scripts/build_nutrition_db.py
  python scripts/build_nutrition_db.py --csv data/cleaned_dataset_with_labels.csv \
    --db database/nutrition_data.duckdb --table nutrition_data
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd


def normalize_bool_like_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert bool-like string columns to numeric 0/1 for cleaner analytics."""
    bool_like_cols = [c for c in df.columns if c.endswith("_is_sam") or "_is_" in c]
    for col in bool_like_cols:
        lowered = df[col].astype(str).str.strip().str.lower()
        df[col] = lowered.map({"true": 1, "false": 0})
    return df


def build_db(csv_path: Path, db_path: Path, table_name: str) -> tuple[int, int]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path, low_memory=False)
    df = normalize_bool_like_columns(df)

    conn = duckdb.connect(str(db_path))
    try:
        conn.register("nutrition_data_df", df)
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM nutrition_data_df")
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        col_count = len(df.columns)
    finally:
        conn.close()
    return row_count, col_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Build persistent DuckDB from nutrition CSV")
    parser.add_argument("--csv", default="data/cleaned_dataset_with_labels.csv", help="Path to labeled CSV")
    parser.add_argument("--db", default="database/nutrition_data.duckdb", help="Output DuckDB path")
    parser.add_argument("--table", default="nutrition_data", help="Destination table name")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    db_path = Path(args.db)

    try:
        row_count, col_count = build_db(csv_path=csv_path, db_path=db_path, table_name=args.table)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Built DuckDB: {db_path}")
    print(f"Table: {args.table}")
    print(f"Rows: {row_count}")
    print(f"Columns: {col_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
