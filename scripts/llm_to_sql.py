#!/usr/bin/env python3
"""
LLM-to-SQL for nutritional analytics using DuckDB + LangChain.

Modes:
1) Single question:
   python scripts/llm_to_sql.py "What is the prevalence of SAM in March?"

2) Batch from file (one query per line):
   python scripts/llm_to_sql.py --queries-file "data/queries /queries.txt"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.text_sql.config import get_nutrition_settings
from src.text_sql.service import (
    compare_generated_with_verified,
    parse_verified_sql_by_query,
    read_queries_file,
    run_single_question,
    save_batch_outputs,
)


def _resolve_repo_path(p: Path) -> Path:
    return p if p.is_absolute() else (ROOT / p).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-to-SQL for cleaned nutrition dataset")
    parser.add_argument("question", nargs="*", help="Natural language question")
    parser.add_argument(
        "--queries-file",
        help="Path to text file with one natural-language query per line",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to nutrition_text_to_sql.yaml (default: configs/nutrition_text_to_sql.yaml under repo root)",
    )
    parser.add_argument("--db", default=None, help="Path to DuckDB file (default from config YAML)")
    parser.add_argument("--model", default=None, help="OpenAI-compatible chat model (default from config / env)")
    parser.add_argument("--temperature", type=float, default=None, help="LLM temperature")
    parser.add_argument("--top-k", type=int, default=5, help="Preferred max rows in response")
    parser.add_argument("--output-dir", default=None, help="Directory for saved batch trace files")
    parser.add_argument(
        "--verified-sql-file",
        default=None,
        help="Path to verified SQL file used for result comparison in batch mode",
    )
    args = parser.parse_args()

    cfg_path = args.config.resolve() if args.config else None
    settings = get_nutrition_settings(cfg_path)

    db_path = _resolve_repo_path(Path(args.db)) if args.db else settings.database_default_path
    output_dir = _resolve_repo_path(Path(args.output_dir)) if args.output_dir else settings.output_dir
    verified_sql_path = (
        _resolve_repo_path(Path(args.verified_sql_file))
        if args.verified_sql_file
        else settings.verified_sql_path
    )
    model = args.model if args.model is not None else settings.llm_model
    temperature = settings.llm_temperature if args.temperature is None else float(args.temperature)

    if not args.queries_file and not args.question:
        print("Error: provide either a question or --queries-file")
        return 1
    if args.queries_file and args.question:
        print("Error: use either a single question OR --queries-file, not both")
        return 1

    config_kw = {"config_path": cfg_path} if cfg_path else {}

    try:
        if args.queries_file:
            queries_file = _resolve_repo_path(Path(args.queries_file))
            queries = read_queries_file(queries_file)
            verified_sql_by_query = parse_verified_sql_by_query(verified_sql_path)
            print(f"Loaded verified SQL for {len(verified_sql_by_query)} query indices")
            results: list[dict] = []
            for i, query in enumerate(queries, start=1):
                print(f"Running query {i}/{len(queries)}: {query}")
                run_result = run_single_question(
                    question=query,
                    db_path=db_path,
                    model=model,
                    temperature=temperature,
                    top_k=args.top_k,
                    **config_kw,
                )
                verified_sql_list = verified_sql_by_query.get(i, [])
                comparison = compare_generated_with_verified(
                    db_path=db_path,
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
                output_dir=output_dir,
                queries_file=queries_file,
                db_path=db_path,
                results=results,
            )
            print(f"\nSaved JSON trace: {json_path}")
            return 0

        question = " ".join(args.question)
        print(f"Question: {question}\n")
        run_result = run_single_question(
            question=question,
            db_path=db_path,
            model=model,
            temperature=temperature,
            top_k=args.top_k,
            **config_kw,
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


if __name__ == "__main__":
    raise SystemExit(main())
