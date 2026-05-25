from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from langchain_community.utilities import SQLDatabase


def open_sql_database(db_path: Path) -> SQLDatabase:
    return SQLDatabase.from_uri(f"duckdb:///{db_path}")

def is_read_only_sql(sql: str) -> bool:
    normalized = sql.strip().lower().lstrip("(")
    return normalized.startswith("select") or normalized.startswith("with")

def execute_sql(db: SQLDatabase, sql: str) -> dict[str, Any]:
    if not is_read_only_sql(sql):
        return {
            "ok": False,
            "raw_output": None,
            "error": "Blocked non-read-only SQL. Only SELECT/WITH statements are allowed.",
        }
    try:
        raw_output = db.run(sql, include_columns=True)
        return {"ok": True, "raw_output": raw_output, "error": None}
    except Exception as exc:
        return {"ok": False, "raw_output": None, "error": str(exc)}

def extract_exec_items(sql_exec: dict[str, Any], generated_sql_list: list[str]) -> list[dict[str, Any]]:
    if sql_exec.get("results"):
        return sql_exec["results"]
    return [
        {
            "sql": generated_sql_list[0] if generated_sql_list else "",
            "ok": sql_exec.get("ok", False),
            "raw_output": sql_exec.get("raw_output"),
            "error": sql_exec.get("error"),
        }
    ]