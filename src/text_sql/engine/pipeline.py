from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ..adapters.providers import (
    FileTemplatePromptBuilder,
    LangChainOpenAIChatClient,
    LangChainReadOnlyExecutor,
    SettingsBackedSchemaProvider,
    cached_chat_openai,
)
from ..config.settings import NutriSqlSettings, get_nutrition_settings
from ..grounding.schema import warmup_schema_cache
from ..sql.execute import open_sql_database
from ..sql.extract import extract_sql


class TextToSQLEngine:
    """Orchestrates schema grounding → prompt → LLM → SQL extract → execute → optional repair."""

    def __init__(self, settings: NutriSqlSettings) -> None:
        self.settings = settings

    @classmethod
    def from_config(cls, config_path: Path | None = None) -> TextToSQLEngine:
        return cls(get_nutrition_settings(config_path))

    def warmup(self, db_path: Path, model: str, temperature: float) -> None:
        warmup_schema_cache(self.settings, db_path)
        cached_chat_openai(model=model, temperature=temperature)

    def run_single_question(
        self,
        question: str,
        db_path: Path,
        model: str,
        temperature: float,
        top_k: int,
    ) -> dict[str, Any]:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set. Export it or add it to .env.")
        if not db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}. Build it first with "
                "python scripts/build_nutrition_db.py"
            )

        t_start = time.perf_counter()

        schema = SettingsBackedSchemaProvider(self.settings)
        table_info, sample_rows_text = schema.table_info_and_samples(db_path)
        prompt = FileTemplatePromptBuilder(self.settings.prompt_template_path).build(
            table_info=table_info,
            sample_rows_text=sample_rows_text,
            top_k=top_k,
        )
        llm = LangChainOpenAIChatClient(model=model, temperature=temperature)
        llm_input = f"{prompt}\n\nUser question:\n{question}"
        llm_raw_output, usage = llm.complete_tracked(llm_input)
        sql_query = extract_sql(llm_raw_output)
        llm_calls = 1

        db = open_sql_database(db_path)
        executor = LangChainReadOnlyExecutor(db)
        first_pass_exec = executor.execute(sql_query)
        first_pass_exec_ok: bool = bool(first_pass_exec.get("ok", False))
        sql_exec = first_pass_exec

        repaired_sql: str | None = None
        repair_llm_output: str | None = None
        if not first_pass_exec_ok and self.settings.repair_enabled:
            repair_prompt = (
                "Fix the DuckDB SQL query so it executes successfully.\n"
                "Return only corrected SQL, no explanation.\n\n"
                f"Broken SQL:\n{sql_query}\n\n"
                f"Execution error:\n{first_pass_exec.get('error')}\n"
            )
            repair_llm_output, repair_usage = llm.complete_tracked(repair_prompt)
            repaired_sql = extract_sql(repair_llm_output)
            sql_exec = executor.execute(repaired_sql)
            llm_calls += 1
            # Merge token counts from both calls
            if repair_usage:
                usage = {
                    "prompt_tokens": (usage.get("prompt_tokens") or 0)
                    + (repair_usage.get("prompt_tokens") or 0),
                    "completion_tokens": (usage.get("completion_tokens") or 0)
                    + (repair_usage.get("completion_tokens") or 0),
                    "total_tokens": (usage.get("total_tokens") or 0)
                    + (repair_usage.get("total_tokens") or 0),
                }

        latency_ms = (time.perf_counter() - t_start) * 1000.0

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
            # Eval instrumentation fields
            "first_pass_exec_ok": first_pass_exec_ok,
            "latency_ms": latency_ms,
            "llm_calls": llm_calls,
            "usage": usage,
        }
