#!/usr/bin/env python3
"""
LLM-to-SQL for nutritional analytics using DuckDB + LangChain.

Modes:
1) Single question:
   python scripts/llm_to_sql.py "What is the prevalence of SAM in March?"

2) Batch from file (one query per line):
   python scripts/llm_to_sql.py --queries-file "data/queries /queries.txt"

Hydra overrides (after legacy flags), e.g.:
   python scripts/llm_to_sql.py llm.model=gpt-4o-mini "What is SAM prevalence?"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from hydra.utils import get_original_cwd, instantiate

from pipeline.paths import resolve_config_path
from pipeline.service import (
    compare_generated_with_verified,
    parse_verified_sql_by_query,
    read_queries_file,
    run_single_question,
    save_batch_outputs,
)

CONF_DIR = ROOT / "conf"


def partition_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Hydra overrides (key=value, +...) vs remainder for argparse."""
    hydra_overrides: list[str] = []
    rest: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a in ("-h", "--help"):
            rest.append(a)
            i += 1
            continue
        if a == "--queries-file" and i + 1 < n:
            rest.extend([a, argv[i + 1]])
            i += 2
            continue
        if a in {
            "--db",
            "--model",
            "--temperature",
            "--top-k",
            "--output-dir",
            "--verified-sql-file",
        } and i + 1 < n:
            rest.extend([a, argv[i + 1]])
            i += 2
            continue
        if a.startswith("+"):
            hydra_overrides.append(a)
            i += 1
            continue
        if "=" in a and not a.startswith("--"):
            hydra_overrides.append(a)
            i += 1
            continue
        rest.append(a)
        i += 1
    return hydra_overrides, rest


def legacy_to_overrides(ns: argparse.Namespace) -> list[str]:
    o: list[str] = []
    if ns.db is not None:
        o.append(f"paths.db_path={ns.db}")
    if ns.model is not None:
        o.append(f"llm.model={ns.model}")
    if ns.temperature is not None:
        o.append(f"llm.temperature={ns.temperature}")
    if ns.top_k is not None:
        o.append(f"llm.top_k={ns.top_k}")
    if ns.output_dir is not None:
        o.append(f"paths.output_dir={ns.output_dir}")
    if ns.verified_sql_file is not None:
        o.append(f"paths.verified_sql_path={ns.verified_sql_file}")
    return o


def main() -> int:
    hydra_part, rest = partition_argv(sys.argv[1:])
    parser = argparse.ArgumentParser(description="LLM-to-SQL for cleaned nutrition dataset")
    parser.add_argument("question", nargs="*", help="Natural language question")
    parser.add_argument(
        "--queries-file",
        dest="queries_file",
        default=None,
        help="Path to text file with one natural-language query per line",
    )
    parser.add_argument("--db", default=None, help="Path to DuckDB file")
    parser.add_argument("--model", default=None, help="OpenAI-compatible chat model")
    parser.add_argument("--temperature", type=float, default=None, help="LLM temperature")
    parser.add_argument("--top-k", type=int, dest="top_k", default=None, help="Preferred max rows in response")
    parser.add_argument("--output-dir", dest="output_dir", default=None, help="Directory for saved batch trace files")
    parser.add_argument(
        "--verified-sql-file",
        dest="verified_sql_file",
        default=None,
        help="Path to verified SQL file used for result comparison in batch mode",
    )
    args = parser.parse_args(rest)

    legacy_overrides = legacy_to_overrides(args)
    overrides = legacy_overrides + hydra_part

    GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=str(CONF_DIR)):
        cfg = compose(config_name="config", overrides=overrides)

    orig = Path(get_original_cwd())
    db_path = resolve_config_path(cfg.paths.db_path, orig)
    output_dir = resolve_config_path(cfg.paths.output_dir, orig)
    verified_sql_path = resolve_config_path(cfg.paths.verified_sql_path, orig)

    merged_model = os.getenv("MODEL_NAME", cfg.llm.model)
    merged_temp = float(os.getenv("TEMPERATURE", str(cfg.llm.temperature)))
    merged_top_k = int(os.getenv("TOP_K", str(cfg.llm.top_k)))
    llm = instantiate(
        {
            "_target_": "langchain_openai.ChatOpenAI",
            "model": merged_model,
            "temperature": merged_temp,
        },
        _convert_="all",
    )

    if not args.queries_file and not args.question:
        print("Error: provide either a question or --queries-file")
        return 1
    if args.queries_file and args.question:
        print("Error: use either a single question OR --queries-file, not both")
        return 1

    try:
        if args.queries_file:
            queries_file = Path(args.queries_file)
            if not queries_file.is_absolute():
                queries_file = (orig / queries_file).resolve()
            queries = read_queries_file(queries_file)
            verified_sql_by_query = parse_verified_sql_by_query(verified_sql_path)
            print(f"Loaded verified SQL for {len(verified_sql_by_query)} query indices")
            results: list[dict] = []
            for i, query in enumerate(queries, start=1):
                print(f"Running query {i}/{len(queries)}: {query}")
                run_result = run_single_question(
                    question=query,
                    db_path=db_path,
                    model=merged_model,
                    temperature=merged_temp,
                    top_k=merged_top_k,
                    llm=llm,
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


if __name__ == "__main__":
    raise SystemExit(main())
