#!/usr/bin/env python3
"""
LLM-to-SQL for analytics using DuckDB + LangChain.

This is a **pure Hydra** entry point: inputs come from composed YAML + Hydra overrides.
No argparse/legacy flags; use config keys directly.

Modes:
1) Single question:
   python scripts/llm_to_sql.py question="What is the prevalence of SAM in March?"

2) Batch from file (one query per line):
   python scripts/llm_to_sql.py queries_file="data/queries/queries.txt"

Hydra overrides (examples):
   python scripts/llm_to_sql.py dataset=bird llm.model=gpt-4o-mini question="What is SAM prevalence?"

`--help` is Hydra's help (config groups and defaults), like the Gradio app.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

import hydra
from hydra.utils import get_original_cwd, instantiate
from omegaconf import DictConfig

from pipeline.dataset_runtime import DatasetRuntime
from pipeline.service import (
    compare_generated_with_verified,
    load_verified_sql_by_question_id,
    read_queries_file,
    run_single_question,
    save_batch_outputs,
)

def _run(cfg: DictConfig) -> int:
    orig = Path(get_original_cwd())
    runtime = DatasetRuntime.from_hydra(cfg, orig)

    merged_model = os.getenv("MODEL_NAME", cfg.llm.model)
    merged_temp = float(os.getenv("TEMPERATURE", str(cfg.llm.temperature)))
    merged_top_k = int(os.getenv("TOP_K", str(cfg.llm.top_k)))
    llm = instantiate(
        cfg.llm.client,
        _convert_="all",
        model=merged_model,
        temperature=merged_temp,
    )

    question = str(getattr(cfg, "question", "") or "").strip()
    queries_file_raw = getattr(cfg, "queries_file", None)
    queries_file = str(queries_file_raw).strip() if queries_file_raw not in (None, "") else ""

    if question == "" and queries_file == "":
        print('Error: provide either `question="..."` or `queries_file="..."`')
        return 1
    if question != "" and queries_file != "":
        print('Error: use either `question="..."` OR `queries_file="..."`, not both')
        return 1

    try:
        if queries_file != "":
            queries_file_path = Path(queries_file)
            if not queries_file_path.is_absolute():
                queries_file_path = (orig / queries_file_path).resolve()
            queries = read_queries_file(queries_file_path)
            verified_sql_by_query = load_verified_sql_by_question_id(runtime.queries_path)
            print(f"Loaded gold SQL for {len(verified_sql_by_query)} query indices from {runtime.queries_path}")
            results: list[dict] = []
            for i, query in enumerate(queries, start=1):
                print(f"Running query {i}/{len(queries)}: {query}")
                run_result = run_single_question(
                    question=query,
                    runtime=runtime,
                    model=merged_model,
                    temperature=merged_temp,
                    top_k=merged_top_k,
                    llm=llm,
                )
                verified_sql_list = verified_sql_by_query.get(i, [])
                comparison = compare_generated_with_verified(
                    sqlalchemy_uri=runtime.sqlalchemy_uri,
                    schema_tables=list(runtime.schema_tables),
                    sample_sql=runtime.sample_sql,
                    generated_sql_list=run_result.get("generated_sql_list", [run_result["generated_sql"]]),
                    sql_exec=run_result["sql_execution"],
                    verified_sql_list=verified_sql_list,
                )
                results.append(
                    {
                        "query_index": i,
                        "query": query,
                        "llm_input": run_result["llm_input"],
                        "llm_raw_output": run_result["llm_raw_output"],
                        "generated_sql": run_result["generated_sql"],
                        "generated_sql_list": run_result.get("generated_sql_list", [run_result["generated_sql"]]),
                        "template_route": run_result.get("template_route"),
                        "repair_llm_output": run_result["repair_llm_output"],
                        "repaired_sql": run_result["repaired_sql"],
                        "sql_execution": run_result["sql_execution"],
                        "comparison": comparison,
                    }
                )

            json_path = save_batch_outputs(
                output_dir=runtime.output_dir,
                queries_file=queries_file_path,
                db_path=runtime.db_path,
                results=results,
            )
            print(f"\nSaved JSON trace: {json_path}")
            return 0

        print(f"Question: {question}\n")
        run_result = run_single_question(
            question=question,
            runtime=runtime,
            model=merged_model,
            temperature=merged_temp,
            top_k=merged_top_k,
            llm=llm,
        )
        print("Generated SQL:")
        print(run_result["generated_sql"])
        if run_result.get("generated_sql_list") and len(run_result["generated_sql_list"]) > 1:
            for idx, sql_text in enumerate(run_result["generated_sql_list"][1:], start=2):
                print(f"\nAdditional SQL {idx}:")
                print(sql_text)
        print("\nSQL Server Output:")
        if run_result["sql_execution"]["ok"]:
            print(run_result["sql_execution"]["raw_output"])
        else:
            print(f"Error: {run_result['sql_execution']['error']}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


@hydra.main(version_base=None, config_path="../conf", config_name="llm_to_sql")
def main(cfg: DictConfig) -> None:
    raise SystemExit(_run(cfg))


if __name__ == "__main__":
    main()
