"""NL → SQL → execute → optional repair; ad-hoc DuckDB reads."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

from pipeline.db.engine import cached_llm, cached_prompt_context, execute_sql
from pipeline.llm.chains import build_prompt, generate_sql, repair_sql
from pipeline.sql.text import is_read_only_sql


def run_single_question(
    question: str,
    db_path: Path,
    model: str,
    temperature: float,
    top_k: int,
    prefer_verified_templates: bool | None = None,
    llm: ChatOpenAI | None = None,
) -> dict[str, Any]:
    _ = prefer_verified_templates  # Backward-compatible no-op: routing removed.
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set. Export it or add it to .env.")
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}. Build it first with "
            "python scripts/build_nutrition_db.py"
        )

    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_info, sample_rows_text = cached_prompt_context(str(db_path.resolve()))
    prompt = build_prompt(table_info=table_info, sample_rows_text=sample_rows_text, top_k=top_k)
    if llm is None:
        llm = cached_llm(model=model, temperature=temperature)

    llm_raw_output, sql_query = generate_sql(question=question, llm=llm, prompt=prompt)
    sql_exec = execute_sql(db=db, sql=sql_query)
    repaired_sql: str | None = None
    repair_llm_output: str | None = None
    if not sql_exec["ok"]:
        repair_llm_output, repaired_sql = repair_sql(
            llm=llm,
            broken_sql=sql_query,
            error_text=str(sql_exec["error"]),
        )
        sql_exec = execute_sql(db=db, sql=repaired_sql)
    final_sql_list = [repaired_sql] if repaired_sql else [sql_query]
    comparison = {
        "checked": False,
        "verdict": "not_checked",
        "reason": "No in-function verified routing comparison. Use batch comparison against queries_verified.sql.",
        "exact_sql_match": None,
        "same_result": None,
    }
    return {
        "question": question,
        "llm_input": prompt,
        "llm_raw_output": llm_raw_output,
        "generated_sql": sql_query,
        "generated_sql_list": final_sql_list,
        "repair_llm_output": repair_llm_output,
        "repaired_sql": repaired_sql,
        "sql_execution": sql_exec,
        "comparison": comparison,
    }


def run_query_dataframe(db_path: Path, sql: str, limit: int = 200) -> pd.DataFrame:
    """Execute SQL directly with DuckDB and return a display dataframe."""
    if not is_read_only_sql(sql):
        return pd.DataFrame({"error": ["Only SELECT/WITH statements are allowed."]})
    wrapped_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS q LIMIT {limit}"
    with duckdb.connect(str(db_path)) as conn:
        return conn.execute(wrapped_sql).fetchdf()
