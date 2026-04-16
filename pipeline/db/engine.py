"""LangChain SQLDatabase access, LLM client cache, and query execution."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

from pipeline.sql.text import is_read_only_sql


def prepare_db_context(db_path: Path) -> tuple[SQLDatabase, str, str]:
    """Open the DB, return LangChain wrapper plus table info and escaped sample rows for prompts."""
    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_info = db.get_table_info(["nutrition_data"])
    sample_rows_text = db.run("SELECT * FROM nutrition_data LIMIT 3")
    # Brace-escape so str.format / f-strings in prompts do not treat JSON-like cells as fields.
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return db, table_info, sample_rows_text


@lru_cache(maxsize=4)
def cached_prompt_context(db_path_str: str) -> tuple[str, str]:
    """Cached (table_info, sample_rows_text) for prompt building; avoids reopening DB each query."""
    db = SQLDatabase.from_uri(f"duckdb:///{db_path_str}")
    table_info = db.get_table_info(["nutrition_data"])
    sample_rows_text = db.run("SELECT * FROM nutrition_data LIMIT 3")
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return table_info, sample_rows_text


@lru_cache(maxsize=8)
def cached_llm(model: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(model=model, temperature=temperature)


def warmup_runtime(db_path: Path, model: str, temperature: float) -> None:
    """Prime cached schema/sample context and LLM client to reduce first-query latency."""
    if not db_path.exists():
        return
    cached_prompt_context(str(db_path.resolve()))
    cached_llm(model=model, temperature=temperature)


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


def parse_raw_output(raw_output: Any) -> Any:
    if raw_output is None:
        return None
    text = str(raw_output).strip()
    if not text:
        return []
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text
