#!/usr/bin/env python3
"""Evaluation runner for the NL→SQL pipeline.

Runs every question in the benchmark against the LLM pipeline, compares results
to verified SQL, aggregates metrics, and writes a structured EvalReport JSON.

Usage — full eval with benchmark tags:
  python scripts/run_eval.py

Usage — override db / model:
  python scripts/run_eval.py --db database/nutrition_data.duckdb --model gpt-4o

Usage — use plain queries.txt without category tags:
  python scripts/run_eval.py --no-benchmark-tags

Output:
  outputs/eval_reports/eval_<timestamp>.json
  outputs/eval_reports/eval_latest.json  (symlink-style copy)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.text_sql.config import get_nutrition_settings
from src.text_sql.eval import aggregate_verdicts
from src.text_sql.service import (
    compare_generated_with_verified,
    parse_verified_sql_by_query,
    read_queries_file,
    run_single_question,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(p: Path) -> Path:
    return p if p.is_absolute() else (ROOT / p).resolve()


def _load_benchmark(path: Path) -> dict[int, dict]:
    """Return {query_index: entry} from eval_benchmark.json.

    Each entry is expected to have at least ``query_index`` and ``category``.
    """
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {int(e["query_index"]): e for e in entries}


def _preview(value: object, *, max_chars: int = 500) -> str:
    """Single-line preview for terminal comparison output."""
    if value is None:
        return "<none>"
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _fallback_generated_items(run_result: dict) -> list[dict]:
    sql_list = run_result.get("generated_sql_list") or [run_result.get("generated_sql", "")]
    sql_exec = run_result.get("sql_execution") or {}
    return [
        {
            "sql": sql_list[0] if sql_list else "",
            "ok": sql_exec.get("ok", False),
            "raw_output": sql_exec.get("raw_output"),
            "error": sql_exec.get("error"),
        }
    ]


def _print_result_items(label: str, items: object) -> None:
    if not items:
        print(f"         {label}: <none>")
        return
    if not isinstance(items, list):
        print(f"         {label}: {_preview(items)}")
        return

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            print(f"         {label} {idx}: {_preview(item)}")
            continue
        if item.get("ok"):
            print(f"         {label} {idx}: {_preview(item.get('raw_output'))}")
        else:
            print(f"         {label} {idx} ERROR: {_preview(item.get('error'))}")


def _print_comparison_details(comparison: dict, run_result: dict) -> None:
    reason = comparison.get("reason")
    if reason:
        print(f"         reason: {reason}")

    generated_results = comparison.get("generated_results")
    if generated_results is None:
        generated_results = _fallback_generated_items(run_result)

    _print_result_items("generated", generated_results)
    _print_result_items("verified", comparison.get("verified_results"))


def _print_summary(report) -> None:  # type: ignore[type-arg]
    """Print a human-readable summary table to stdout."""
    sep = "-" * 52
    print(sep)
    print("EVAL REPORT SUMMARY")
    print(sep)
    print(f"  Model          : {report.model}")
    print(f"  DB             : {report.db_path}")
    print(f"  Total queries  : {report.total_queries}")
    print(sep)
    print("EXECUTION ACCURACY (Layer 1)")
    ea = report.execution_accuracy
    print(f"  execution_accuracy   : {ea * 100:.1f}%" if ea is not None else "  execution_accuracy   : n/a")
    print(f"  not_checked_rate     : {report.not_checked_rate * 100:.1f}%")
    print(sep)
    print("RELIABILITY (Layer 2)")
    fp = report.first_pass_exec_rate
    rs = report.repair_success_rate
    print(f"  first_pass_exec_rate : {fp * 100:.1f}%" if fp is not None else "  first_pass_exec_rate : n/a")
    print(f"  repair_rate          : {report.repair_rate * 100:.1f}%")
    print(f"  repair_success_rate  : {rs * 100:.1f}%" if rs is not None else "  repair_success_rate  : n/a (no repairs)")
    print(f"  total_failure_rate   : {report.total_failure_rate * 100:.1f}%")
    print(sep)
    if report.avg_latency_ms is not None:
        print("COST & LATENCY (Layer 3)")
        print(f"  avg_latency_ms       : {report.avg_latency_ms:.0f} ms")
        print(f"  p95_latency_ms       : {report.p95_latency_ms:.0f} ms")
        if report.total_tokens is not None:
            print(f"  total_tokens         : {report.total_tokens:,}")
            print(f"    prompt_tokens      : {report.total_prompt_tokens:,}")
            print(f"    completion_tokens  : {report.total_completion_tokens:,}")
        if report.avg_llm_calls_per_query is not None:
            print(f"  avg_llm_calls/query  : {report.avg_llm_calls_per_query:.2f}")
        print(sep)
    if report.per_category:
        print("PER-CATEGORY ACCURACY (Layer 4)")
        for cat, s in report.per_category.items():
            acc = s["accuracy"]
            acc_str = f"{acc * 100:.1f}%" if acc is not None else "n/a"
            print(f"  {cat:<25} n={s['n']:2d}  accuracy={acc_str}")
        print(sep)
    print("PER-QUERY VERDICTS")
    for pq in report.per_query:
        idx = pq.get("query_index", "?")
        verdict = pq.get("verdict", "?")
        repaired = " [repaired]" if pq.get("repaired") else ""
        cat = f"  [{pq['category']}]" if pq.get("category") else ""
        lat = f"  {pq['latency_ms']:.0f}ms" if pq.get("latency_ms") is not None else ""
        q = pq.get("question", "")[:60]
        print(f"  Q{idx:>2}: {verdict:<11}{repaired}{cat}{lat}  {q}")
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run full NL→SQL evaluation and write EvalReport JSON"
    )
    parser.add_argument(
        "--benchmark-file",
        type=Path,
        default=None,
        help="Path to eval_benchmark.json (default: data/queries/eval_benchmark.json)",
    )
    parser.add_argument(
        "--no-benchmark-tags",
        action="store_true",
        help="Skip category tagging — use plain queries.txt without eval_benchmark.json",
    )
    parser.add_argument(
        "--queries-file",
        type=Path,
        default=None,
        help="Plain text queries file (one per line); used when --no-benchmark-tags is set "
        "or when benchmark file is absent (default from config)",
    )
    parser.add_argument(
        "--verified-sql-file",
        type=Path,
        default=None,
        help="Path to queries_verified.sql (default from config)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to DuckDB file (default from config)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI-compatible model name (default from config / env)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="LLM temperature (default from config)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Preferred max rows in response (default: 5)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for eval report JSON (default: outputs/eval_reports)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to nutrition_text_to_sql.yaml",
    )
    args = parser.parse_args()

    cfg_path = _resolve(args.config) if args.config else None
    settings = get_nutrition_settings(cfg_path)

    db_path = _resolve(args.db) if args.db else settings.database_default_path
    output_dir = _resolve(args.output_dir) if args.output_dir else ROOT / "outputs" / "eval_reports"
    verified_sql_path = _resolve(args.verified_sql_file) if args.verified_sql_file else settings.verified_sql_path
    model = args.model if args.model is not None else settings.llm_model
    temperature = settings.llm_temperature if args.temperature is None else float(args.temperature)
    config_kw = {"config_path": cfg_path} if cfg_path else {}

    # --- Resolve benchmark / queries source ---
    default_benchmark_path = ROOT / "data" / "queries" / "eval_benchmark.json"
    benchmark_path = _resolve(args.benchmark_file) if args.benchmark_file else default_benchmark_path

    use_tags = not args.no_benchmark_tags and benchmark_path.exists()
    benchmark_by_index: dict[int, dict] = {}

    if use_tags:
        benchmark_by_index = _load_benchmark(benchmark_path)
        questions = [benchmark_by_index[i]["question"] for i in sorted(benchmark_by_index)]
        print(f"Loaded {len(questions)} questions from {benchmark_path.name} (with category tags)")
    else:
        queries_file = _resolve(args.queries_file) if args.queries_file else settings.queries_txt_path
        questions = read_queries_file(queries_file)
        print(f"Loaded {len(questions)} questions from {queries_file.name} (no category tags)")

    # --- Load verified SQL ---
    verified_sql_by_query = parse_verified_sql_by_query(verified_sql_path)
    print(f"Loaded verified SQL for {len(verified_sql_by_query)} query indices")
    print(f"Model: {model}  |  DB: {db_path.name}\n")

    # --- Run each question ---
    results: list[dict] = []
    for i, question in enumerate(questions, start=1):
        print(f"[{i:2d}/{len(questions)}] {question[:70]}")
        try:
            run_result = run_single_question(
                question=question,
                db_path=db_path,
                model=model,
                temperature=temperature,
                top_k=args.top_k,
                **config_kw,
            )
        except Exception as exc:
            print(f"         ERROR during run_single_question: {exc}")
            run_result = {
                "question": question,
                "llm_input": "",
                "llm_raw_output": "",
                "generated_sql": "",
                "generated_sql_list": [],
                "repair_llm_output": None,
                "repaired_sql": None,
                "sql_execution": {"ok": False, "raw_output": None, "error": str(exc)},
                "comparison": {"verdict": "wrong", "checked": True, "reason": f"Exception: {exc}"},
                "first_pass_exec_ok": False,
                "latency_ms": None,
                "llm_calls": 0,
                "usage": {},
            }

        verified_sql_list = verified_sql_by_query.get(i, [])
        comparison = compare_generated_with_verified(
            db_path=db_path,
            generated_sql_list=run_result.get("generated_sql_list", [run_result.get("generated_sql", "")]),
            sql_exec=run_result["sql_execution"],
            verified_sql_list=verified_sql_list,
        )

        entry: dict = {
            "query_index": i,
            "question": question,
            **run_result,
            "comparison": comparison,
        }

        # Inject category from benchmark JSON if available
        if use_tags and i in benchmark_by_index:
            entry["category"] = benchmark_by_index[i].get("category")

        verdict = comparison.get("verdict", "not_checked")
        repaired = run_result.get("repaired_sql") is not None
        lat = run_result.get("latency_ms")
        lat_str = f"  {lat:.0f}ms" if lat is not None else ""
        rep_str = " [repaired]" if repaired else ""
        print(f"         {verdict.upper()}{rep_str}{lat_str}")
        _print_comparison_details(comparison, run_result)

        results.append(entry)

    # --- Aggregate ---
    report = aggregate_verdicts(results, model=model, db_path=str(db_path))

    # --- Serialize ---
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"eval_{timestamp}.json"
    latest_path = output_dir / "eval_latest.json"

    report_dict = {
        "generated_at": datetime.now().isoformat(),
        "model": report.model,
        "db_path": report.db_path,
        "total_queries": report.total_queries,
        "execution_accuracy": report.execution_accuracy,
        "not_checked_rate": report.not_checked_rate,
        "first_pass_exec_rate": report.first_pass_exec_rate,
        "repair_rate": report.repair_rate,
        "repair_success_rate": report.repair_success_rate,
        "total_failure_rate": report.total_failure_rate,
        "avg_latency_ms": report.avg_latency_ms,
        "p95_latency_ms": report.p95_latency_ms,
        "total_prompt_tokens": report.total_prompt_tokens,
        "total_completion_tokens": report.total_completion_tokens,
        "total_tokens": report.total_tokens,
        "avg_llm_calls_per_query": report.avg_llm_calls_per_query,
        "per_category": report.per_category,
        "per_query": report.per_query,
        "raw_results": [
            {
                k: v
                for k, v in r.items()
                if k not in ("llm_input",)  # omit large prompt text from report
            }
            for r in results
        ],
    }

    serialized = json.dumps(report_dict, indent=2, default=str)
    report_path.write_text(serialized, encoding="utf-8")
    latest_path.write_text(serialized, encoding="utf-8")

    print()
    _print_summary(report)
    print(f"\nReport saved to: {report_path}")
    print(f"Latest copy   : {latest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
