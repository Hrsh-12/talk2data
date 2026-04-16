#!/usr/bin/env python3
"""
Normalize an existing DuckDB into the repo's canonical runtime format.

The NL→SQL runtime expects (by default):
  - database/nutrition_data.duckdb
  - table: nutrition_data

This script materializes `nutrition_data` into the output DB from an input DB/table.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def _pick_single_table(conn: duckdb.DuckDBPyConnection) -> str:
    rows = conn.execute("SHOW TABLES").fetchall()
    tables = [r[0] for r in rows]
    if not tables:
        raise ValueError("No tables found in input DuckDB")
    if len(tables) == 1:
        return tables[0]
    raise ValueError(
        "Multiple tables found in input DuckDB. Provide --in-table explicitly. "
        f"Found: {tables}"
    )


def normalize(in_db: Path, out_db: Path, in_table: str | None) -> None:
    if not in_db.exists():
        raise FileNotFoundError(f"Input DuckDB not found: {in_db}")
    out_db.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(in_db), read_only=True) as src:
        src_table = in_table.strip() if in_table else _pick_single_table(src)
        # Create an ATTACH-friendly absolute path for clarity.
        src_path = str(in_db.resolve())

    with duckdb.connect(str(out_db)) as dst:
        dst.execute("PRAGMA threads=4;")
        dst.execute(f"ATTACH '{src_path}' AS src (READ_ONLY);")
        dst.execute("DROP TABLE IF EXISTS nutrition_data;")
        dst.execute(f"CREATE TABLE nutrition_data AS SELECT * FROM src.{src_table};")
        row_count = dst.execute("SELECT COUNT(*) FROM nutrition_data;").fetchone()[0]
        col_count = dst.execute("DESCRIBE nutrition_data;").fetchall()
        dst.execute("DETACH src;")

    if row_count <= 0:
        raise ValueError("Normalization produced an empty nutrition_data table")
    if len(col_count) <= 0:
        raise ValueError("Normalization produced a table with no columns")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Materialize canonical database/nutrition_data.duckdb (nutrition_data table) from an existing DuckDB"
    )
    p.add_argument(
        "--in-db",
        type=Path,
        default=Path("database/nutrition_data_filtered.duckdb"),
        help="Input DuckDB path (source)",
    )
    p.add_argument(
        "--in-table",
        default=None,
        help="Source table name in --in-db (optional if input DB has exactly one table)",
    )
    p.add_argument(
        "--out-db",
        type=Path,
        default=Path("database/nutrition_data.duckdb"),
        help="Output DuckDB path (canonical runtime DB)",
    )
    args = p.parse_args()

    try:
        normalize(in_db=args.in_db, out_db=args.out_db, in_table=args.in_table)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Wrote canonical DB: {args.out_db}")
    print("Table: nutrition_data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

