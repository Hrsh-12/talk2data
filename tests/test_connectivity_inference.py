"""Opt-in DuckDB connectivity: set RUN_CONNECTIVITY=1 and point DB_PATH at any DuckDB file."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from langchain_community.utilities import SQLDatabase

REPO = Path(__file__).resolve().parents[1]


def _default_db_path() -> Path:
    raw = os.environ.get("DB_PATH")
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (REPO / raw).resolve()
    home_data = Path.home() / "data" / "nutrition_data.duckdb"
    if home_data.exists():
        return home_data
    return REPO / "database" / "nutrition_data.duckdb"


@pytest.mark.requires_db
def test_duckdb_connect_and_select_one() -> None:
    db_path = _default_db_path()
    if not db_path.exists():
        pytest.skip(f"DuckDB not found at {db_path} (set DB_PATH or build database)")
    with duckdb.connect(str(db_path)) as conn:
        row = conn.execute("SELECT 1 AS x").fetchone()
    assert row == (1,)


@pytest.mark.requires_db
def test_langchain_reflects_existing_table() -> None:
    """Use whatever tables exist in the file — no fixed table name in assertions."""
    db_path = _default_db_path()
    if not db_path.exists():
        pytest.skip(f"DuckDB not found at {db_path}")
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SHOW TABLES").fetchall()
    if not rows:
        pytest.skip("Database has no tables to introspect")
    first_table = str(rows[0][0])
    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    info = db.get_table_info([first_table])
    assert first_table.lower() in info.lower() or "CREATE" in info.upper()