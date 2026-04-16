"""NL → SQL → execute → optional repair; read-only SQL → dataframe."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from sqlalchemy import create_engine, text

from pipeline.dataset_runtime import DatasetRuntime
from pipeline.db.engine import cached_llm, cached_prompt_context, execute_sql
from pipeline.llm.chains import build_prompt, generate_sql, repair_sql
from pipeline.sql.text import is_read_only_sql


def _normalize_evidence(evidence: str | None) -> str | None:
    if evidence is None:
        return None
    s = str(evidence).strip()
    if not s or s.upper() in ("NA", "N/A"):
        return None
    return s


def run_single_question(
    question: str,
    *,
    runtime: DatasetRuntime,
    model: str,
    temperature: float,
    top_k: int,
    llm: ChatOpenAI | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set. Export it or add it to .env.")
    if not runtime.db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {runtime.db_path}. "
            "Build or download the dataset DB and set paths in config / DB_PATH."
        )

    q = question.strip()
    ev = _normalize_evidence(evidence)
    if runtime.inject_evidence and ev:
        q = f"{q}\n\nEvidence:\n{ev}"

    db = SQLDatabase.from_uri(runtime.sqlalchemy_uri)
    table_info, sample_rows_text = cached_prompt_context(
        runtime.sqlalchemy_uri,
        runtime.schema_tables,
        runtime.sample_sql or "",
    )
    prompt = build_prompt(
        prompt_path=runtime.sql_generation_prompt_path,
        table_info=table_info,
        sample_rows_text=sample_rows_text,
        top_k=top_k,
        schema_notes_block=runtime.schema_notes_block(),
        hints_block=runtime.hints_block(),
    )
    if llm is None:
        llm = cached_llm(model=model, temperature=temperature)

    llm_raw_output, sql_query = generate_sql(question=q, llm=llm, prompt=prompt)
    sql_exec = execute_sql(db=db, sql=sql_query)
    repaired_sql: str | None = None
    repair_llm_output: str | None = None
    if not sql_exec["ok"]:
        repair_llm_output, repaired_sql = repair_sql(
            llm=llm,
            broken_sql=sql_query,
            error_text=str(sql_exec["error"]),
            repair_prompt_path=runtime.sql_repair_prompt_path,
        )
        sql_exec = execute_sql(db=db, sql=repaired_sql)
    final_sql_list = [repaired_sql] if repaired_sql else [sql_query]
    comparison = {
        "checked": False,
        "verdict": "not_checked",
        "reason": "No in-function verified routing comparison. Use batch comparison against the query catalog.",
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


def run_query_dataframe(engine_target: Path | str, sql: str, limit: int = 200) -> pd.DataFrame:
    """Execute read-only SQL and return a dataframe (DuckDB file or SQLAlchemy URI)."""
    if not is_read_only_sql(sql):
        return pd.DataFrame({"error": ["Only SELECT/WITH statements are allowed."]})
    wrapped_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS q LIMIT {limit}"
    if isinstance(engine_target, Path):
        with duckdb.connect(str(engine_target)) as conn:
            return conn.execute(wrapped_sql).fetchdf()
    uri = engine_target
    engine = create_engine(uri)
    with engine.connect() as conn:
        return pd.read_sql(text(wrapped_sql), conn)
