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


def execute_sql_list(db: SQLDatabase, sql_list: list[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    all_ok = True
    for sql in sql_list:
        item = execute_sql(db=db, sql=sql)
        results.append({"sql": sql, **item})
        if not item["ok"]:
            all_ok = False
    return {
        "ok": all_ok,
        "raw_output": [r["raw_output"] for r in results],
        "error": None if all_ok else "; ".join(str(r["error"]) for r in results if r["error"]),
        "results": results,
    }


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


def run_query_dataframe(db_path: Path, sql: str, limit: int = 200) -> pd.DataFrame:
    """Execute SQL directly with DuckDB and return a display dataframe."""
    if not is_read_only_sql(sql):
        return pd.DataFrame({"error": ["Only SELECT/WITH statements are allowed."]})
    wrapped_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS q LIMIT {limit}"
    with duckdb.connect(str(db_path)) as conn:
        return conn.execute(wrapped_sql).fetchdf()
