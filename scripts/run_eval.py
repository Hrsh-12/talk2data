#!/usr/bin/env python3
"""Evaluation runner for the NL→SQL pipeline.

Runs every question in the benchmark against the LLM pipeline, compares results
to verified SQL, aggregates metrics, and writes a structured EvalReport JSON.

Usage — full eval with benchmark tags:
  python scripts/run_eval.py

Usage — override db:
  python scripts/run_eval.py --db database/nutrition_data.duckdb

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

from src.text_sql.engine import TextToSQLEngine
from src.text_sql.eval import (
    aggregate_verdicts,
    compare_generated_with_verified,
    parse_verified_sql_by_query,
)
from src.text_sql.utils import (
    get_nutrition_settings,
    read_queries_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(p: Path) -> Path:
    return p if p.is_absolute() else (ROOT / p).resolve()


def _load_benchmark(path: Path) -> tuple[list[dict], bool]:
    """Return (ordered entries, is_noisy) from a single benchmark JSON file.

    Normal benchmarks are sorted by query_index (unique per entry).
    Noisy benchmarks carry a ``noisy_id`` field (same query_index can appear
    multiple times with different noise types) and are sorted by noisy_id.
    Each entry is guaranteed to have ``query_index`` for golden-SQL lookup and
    ``question`` for the LLM prompt.
    """
    entries = json.loads(path.read_text(encoding="utf-8"))
    is_noisy = bool(entries) and "noisy_id" in entries[0]
    if is_noisy:
        return sorted(entries, key=lambda e: int(e["noisy_id"])), True
    return sorted(entries, key=lambda e: int(e["query_index"])), False


def _load_benchmarks(paths: list[Path]) -> tuple[list[dict], bool]:
    """Load one or more benchmark JSON files and return combined entries.

    When multiple files are provided, entries are appended in the order of the
    provided paths. Normal and noisy benchmarks can be mixed together.
    """
    ordered_entries: list[dict] = []
    is_noisy = False
    for path in paths:
        entries, path_is_noisy = _load_benchmark(path)
        ordered_entries.extend(entries)
        is_noisy = is_noisy or path_is_noisy
    return ordered_entries, is_noisy


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
        nargs="+",
        default=None,
        help=(
            "Path to one or more benchmark JSON files. "
            "Default: data/queries/eval_benchmark.json and data/queries/eval_benchmark_noisy.json "
            "when both are present."
        ),
    )
    parser.add_argument(
        "--no-benchmark-tags",
        action="store_true",
        help="Skip category tagging — use plain queries.txt without benchmark JSON files",
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
    model = settings.llm_model
    engine = TextToSQLEngine.from_config(cfg_path, db_path=db_path, top_k=args.top_k)

    # --- Resolve benchmark / queries source ---
    default_benchmark_paths: list[Path] = []
    default_benchmark_paths.append(ROOT / "data" / "queries" / "eval_benchmark.json")
    default_benchmark_paths.append(ROOT / "data" / "queries" / "eval_benchmark_noisy.json")

    if args.benchmark_file:
        benchmark_paths = [_resolve(p) for p in args.benchmark_file]
    else:
        benchmark_paths = [p for p in default_benchmark_paths if p.exists()]

    use_tags = not args.no_benchmark_tags and bool(benchmark_paths)
    ordered_entries: list[dict] = []
    is_noisy: bool = False

    if use_tags:
        ordered_entries, is_noisy = _load_benchmarks(benchmark_paths)
        questions = [e["question"] for e in ordered_entries]
        names = ", ".join(p.name for p in benchmark_paths)
        mode_label = "with category tags"
        if is_noisy:
            mode_label += " (including noisy entries)"
        print(f"Loaded {len(questions)} questions from {names} ({mode_label})")
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
            run_result = engine.run_single_question(
                question=question,
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

        # Resolve the query_index for golden-SQL lookup.
        # For normal benchmarks i == query_index; for noisy benchmarks they diverge.
        entry_meta = ordered_entries[i - 1] if use_tags else {}
        q_idx = int(entry_meta.get("query_index", i))

        verified_sql_list = verified_sql_by_query.get(q_idx, [])
        comparison = compare_generated_with_verified(
            db_path=db_path,
            generated_sql_list=run_result.get("generated_sql_list", [run_result.get("generated_sql", "")]),
            sql_exec=run_result["sql_execution"],
            verified_sql_list=verified_sql_list,
        )

        entry: dict = {
            "query_index": q_idx,
            "question": question,
            **run_result,
            "comparison": comparison,
        }

        # Inject metadata from benchmark JSON if available
        if use_tags:
            entry["category"] = entry_meta.get("category")
            if "noise_type" in entry_meta:
                entry["noise_type"] = entry_meta["noise_type"]
                entry["original_question"] = entry_meta.get("original_question", "")
            if "noisy_id" in entry_meta:
                entry["noisy_id"] = int(entry_meta["noisy_id"])

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

    # --- Per-noise-type breakdown (noisy benchmark only) ---
    per_noise_type: dict = {}
    if is_noisy:
        from collections import defaultdict
        noise_stats: dict = defaultdict(lambda: {"n": 0, "right": 0, "wrong": 0, "not_checked": 0})
        for r in results:
            nt = r.get("noise_type")
            if nt:
                s = noise_stats[nt]
                s["n"] += 1
                v = (r.get("comparison") or {}).get("verdict", "not_checked")
                if v in s:
                    s[v] += 1
        per_noise_type = {
            nt: {
                **s,
                "accuracy": s["right"] / (s["right"] + s["wrong"])
                if (s["right"] + s["wrong"]) > 0 else None,
            }
            for nt, s in sorted(noise_stats.items())
        }

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
        "per_noise_type": per_noise_type,
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

    if per_noise_type:
        sep = "-" * 52
        print(sep)
        print("PER-NOISE-TYPE ACCURACY")
        print(sep)
        for nt, s in per_noise_type.items():
            acc = s["accuracy"]
            acc_str = f"{acc * 100:.1f}%" if acc is not None else "n/a"
            print(f"  {nt:<20} n={s['n']:2d}  accuracy={acc_str}")
        print(sep)

    print(f"\nReport saved to: {report_path}")
    print(f"Latest copy   : {latest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
