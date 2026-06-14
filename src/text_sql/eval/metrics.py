"""Aggregate per-query trace dicts into a structured EvalReport."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CategoryStats:
    n: int = 0
    right: int = 0
    wrong: int = 0
    not_checked: int = 0

    @property
    def accuracy(self) -> float | None:
        checked = self.right + self.wrong
        return self.right / checked if checked > 0 else None


@dataclass
class EvalReport:
    # Run metadata
    model: str
    db_path: str
    total_queries: int

    # Layer 1 — Execution Accuracy
    execution_accuracy: float | None  # right / (right + wrong) over checked queries
    not_checked_rate: float  # not_checked / total

    # Layer 2 — Reliability
    first_pass_exec_rate: float | None  # first-pass SQL execution success / total with known status
    repair_rate: float  # queries that triggered a repair / total
    repair_success_rate: float | None  # repaired queries that ended right / all repaired
    total_failure_rate: float  # verdict == wrong / total

    # Layer 3 — Cost and Latency (None when timing was not captured by the pipeline)
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    total_prompt_tokens: int | None
    total_completion_tokens: int | None
    total_tokens: int | None
    avg_llm_calls_per_query: float | None

    # Layer 4 — Per-category breakdown (empty when no eval_benchmark.json categories injected)
    per_category: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Per-query detail for inspection
    per_query: list[dict[str, Any]] = field(default_factory=list)


def aggregate_verdicts(
    results: list[dict[str, Any]],
    *,
    model: str = "unknown",
    db_path: str = "unknown",
) -> EvalReport:
    """Aggregate per-query trace dicts (from run_eval.py) into an EvalReport.

    Each dict in *results* is expected to carry the fields that
    ``run_single_question()`` returns, plus optionally:
    - ``comparison`` (dict with ``verdict``)
    - ``category`` (str, injected by the eval runner from eval_benchmark.json)
    - ``latency_ms`` (float, injected when timing is enabled)
    - ``usage`` (dict with prompt_tokens / completion_tokens / total_tokens)
    - ``llm_calls`` (int)
    - ``first_pass_exec_ok`` (bool, injected by the pipeline)
    """
    total = len(results)
    if total == 0:
        raise ValueError("results list is empty — nothing to aggregate")

    # Layer 1 counters
    right = wrong = not_checked = 0

    # Layer 2 counters
    first_pass_ok = 0
    first_pass_known = 0
    repaired_count = 0
    repair_right = 0

    # Layer 3 accumulators
    latencies: list[float] = []
    prompt_tokens_total = 0
    completion_tokens_total = 0
    total_tokens_sum = 0
    llm_calls_total = 0
    has_tokens = False
    has_llm_calls = False

    # Layer 4
    category_stats: dict[str, CategoryStats] = {}

    per_query: list[dict[str, Any]] = []

    for r in results:
        comparison = r.get("comparison") or {}
        verdict = comparison.get("verdict", "not_checked")
        category: str | None = r.get("category")

        # --- Layer 1 ---
        if verdict == "right":
            right += 1
        elif verdict == "wrong":
            wrong += 1
        else:
            not_checked += 1

        # --- Layer 2 ---
        repaired = r.get("repaired_sql") is not None
        if repaired:
            repaired_count += 1
            if verdict == "right":
                repair_right += 1

        first_pass_exec_ok: bool | None = r.get("first_pass_exec_ok")
        if first_pass_exec_ok is not None:
            # Explicit flag set by the pipeline
            first_pass_known += 1
            if first_pass_exec_ok:
                first_pass_ok += 1
        elif repaired:
            # Repair was triggered → first pass must have failed
            first_pass_known += 1
        else:
            # No repair → final exec status reflects first pass
            sql_exec_ok = (r.get("sql_execution") or {}).get("ok")
            if sql_exec_ok is not None:
                first_pass_known += 1
                if sql_exec_ok:
                    first_pass_ok += 1

        # --- Layer 3 ---
        latency = r.get("latency_ms")
        if latency is not None:
            latencies.append(float(latency))

        usage = r.get("usage") or {}
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        if pt is not None or ct is not None:
            has_tokens = True
            prompt_tokens_total += pt or 0
            completion_tokens_total += ct or 0
            total_tokens_sum += tt if tt is not None else ((pt or 0) + (ct or 0))

        llm_calls = r.get("llm_calls")
        if llm_calls is not None:
            has_llm_calls = True
            llm_calls_total += int(llm_calls)

        # --- Layer 4 ---
        if category:
            if category not in category_stats:
                category_stats[category] = CategoryStats()
            s = category_stats[category]
            s.n += 1
            if verdict == "right":
                s.right += 1
            elif verdict == "wrong":
                s.wrong += 1
            else:
                s.not_checked += 1

        per_query.append(
            {
                "question": r.get("question", ""),
                "query_index": r.get("query_index"),
                "category": category,
                "noise_type": r.get("noise_type"),
                "original_question": r.get("original_question"),
                "verdict": verdict,
                "repaired": repaired,
                "latency_ms": latency,
                "llm_calls": llm_calls,
                "prompt_tokens": pt,
                "completion_tokens": ct,
            }
        )

    # --- Derived metrics ---
    checked = right + wrong
    execution_accuracy = right / checked if checked > 0 else None
    not_checked_rate = not_checked / total

    first_pass_exec_rate = first_pass_ok / first_pass_known if first_pass_known > 0 else None
    repair_rate = repaired_count / total
    repair_success_rate = repair_right / repaired_count if repaired_count > 0 else None
    total_failure_rate = wrong / total

    avg_latency: float | None = statistics.mean(latencies) if latencies else None
    p95_latency: float | None = None
    if latencies:
        sorted_lat = sorted(latencies)
        p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
        p95_latency = sorted_lat[p95_idx]

    per_category = {
        cat: {
            "n": s.n,
            "right": s.right,
            "wrong": s.wrong,
            "not_checked": s.not_checked,
            "accuracy": s.accuracy,
        }
        for cat, s in sorted(category_stats.items())
    }

    return EvalReport(
        model=model,
        db_path=db_path,
        total_queries=total,
        execution_accuracy=execution_accuracy,
        not_checked_rate=not_checked_rate,
        first_pass_exec_rate=first_pass_exec_rate,
        repair_rate=repair_rate,
        repair_success_rate=repair_success_rate,
        total_failure_rate=total_failure_rate,
        avg_latency_ms=avg_latency,
        p95_latency_ms=p95_latency,
        total_prompt_tokens=prompt_tokens_total if has_tokens else None,
        total_completion_tokens=completion_tokens_total if has_tokens else None,
        total_tokens=total_tokens_sum if has_tokens else None,
        avg_llm_calls_per_query=llm_calls_total / total if has_llm_calls else None,
        per_category=per_category,
        per_query=per_query,
    )
