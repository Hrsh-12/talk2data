from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..results.parse import parse_verified_comparison_value
from ..sql.execute import execute_sql, extract_exec_items, open_sql_database
from ..sql.extract import normalize_sql

_MONTH_TOKEN_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.IGNORECASE,
)


def _normalize_dimension_label(value: Any) -> str:
    """Normalize month-like labels and column names for semantic comparison."""
    text = str(value).strip().lower()
    match = _MONTH_TOKEN_RE.search(text)
    if match:
        return match.group(1).lower()
    return text


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare_numeric_values(
    left: Any,
    right: Any,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, float]:
    diff = abs(float(left) - float(right))
    allowed = max(abs_tol, rel_tol * max(abs(float(left)), abs(float(right)), 1.0))
    return diff <= allowed, diff


def _compare_scalar_values(
    left: Any,
    right: Any,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, float]:
    if _is_numeric(left) and _is_numeric(right):
        return _compare_numeric_values(left, right, abs_tol=abs_tol, rel_tol=rel_tol)

    if isinstance(left, str) and isinstance(right, str):
        return _normalize_dimension_label(left) == _normalize_dimension_label(right), 0.0

    return left == right, 0.0


def _canonicalize_tabular_value(value: Any) -> list[tuple[str, tuple[Any, ...]]] | None:
    """Convert tall or wide tabular SQL output into comparable label/value rows."""
    if not isinstance(value, list) or not value or not all(isinstance(row, dict) for row in value):
        return None

    if len(value) == 1 and len(value[0]) > 1:
        entries: list[tuple[str, tuple[Any, ...]]] = []
        for key, cell in value[0].items():
            label = _normalize_dimension_label(key)
            if _is_numeric(cell):
                entries.append((label, (cell,)))
            elif isinstance(cell, str):
                entries.append((label, (cell,)))
            else:
                return None
        return sorted(entries, key=lambda item: item[0])

    if len(value) >= 1 and all(len(row) >= 2 for row in value):
        entries = []
        for row in value:
            string_fields = [(key, cell) for key, cell in row.items() if isinstance(cell, str)]
            numeric_fields = [cell for cell in row.values() if _is_numeric(cell)]

            if len(string_fields) == 1 and numeric_fields:
                label = _normalize_dimension_label(string_fields[0][1])
                entries.append((label, tuple(numeric_fields)))
                continue

            if len(string_fields) == 0 and numeric_fields:
                for key, cell in row.items():
                    if _is_numeric(cell):
                        entries.append((_normalize_dimension_label(key), (cell,)))
                continue

            return None

        return sorted(entries, key=lambda item: item[0])

    return None


def _compare_canonical_tables(
    left: list[tuple[str, tuple[Any, ...]]],
    right: list[tuple[str, tuple[Any, ...]]],
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, float]:
    if len(left) != len(right):
        return False, 0.0

    max_diff = 0.0
    for (left_label, left_values), (right_label, right_values) in zip(left, right):
        if left_label != right_label:
            return False, max_diff
        if len(left_values) != len(right_values):
            return False, max_diff
        for left_value, right_value in zip(left_values, right_values):
            match, diff = _compare_scalar_values(
                left_value,
                right_value,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            if not match:
                return False, max_diff
            max_diff = max(max_diff, diff)

    return True, max_diff


def _comparison_sort_key(value: Any) -> str:
    """Stable row ordering key that ignores SQL output aliases."""
    if isinstance(value, dict):
        return repr([_comparison_sort_key(v) for v in value.values()])
    if isinstance(value, (list, tuple)):
        return repr([_comparison_sort_key(v) for v in value])
    return repr(value)


def compare_structured_values(
    left: Any,
    right: Any,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, bool, float]:
    max_diff = 0.0

    if _is_numeric(left) and _is_numeric(right):
        match, diff = _compare_numeric_values(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
        return match, True, diff

    if isinstance(left, str) and isinstance(right, str):
        match, diff = _compare_scalar_values(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
        return match, True, diff

    if type(left) is not type(right):
        left_canonical = _canonicalize_tabular_value(left)
        right_canonical = _canonicalize_tabular_value(right)
        if left_canonical is not None and right_canonical is not None:
            match, diff = _compare_canonical_tables(
                left_canonical,
                right_canonical,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            return match, True, diff
        return False, False, max_diff

    if isinstance(left, list):
        if len(left) != len(right):
            left_canonical = _canonicalize_tabular_value(left)
            right_canonical = _canonicalize_tabular_value(right)
            if left_canonical is not None and right_canonical is not None:
                match, diff = _compare_canonical_tables(
                    left_canonical,
                    right_canonical,
                    abs_tol=abs_tol,
                    rel_tol=rel_tol,
                )
                return match, True, diff
            return False, False, max_diff

        left_items = sorted(left, key=_comparison_sort_key)
        right_items = sorted(right, key=_comparison_sort_key)
        all_match = True
        all_shape = True
        for l_item, r_item in zip(left_items, right_items):
            match, shape, diff = compare_structured_values(
                l_item,
                r_item,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            all_match = all_match and match
            all_shape = all_shape and shape
            max_diff = max(max_diff, diff)
        return all_match, all_shape, max_diff

    if isinstance(left, tuple):
        if len(left) != len(right):
            return False, False, max_diff
        all_match = True
        all_shape = True
        for l_item, r_item in zip(left, right):
            match, shape, diff = compare_structured_values(
                l_item,
                r_item,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            all_match = all_match and match
            all_shape = all_shape and shape
            max_diff = max(max_diff, diff)
        return all_match, all_shape, max_diff

    if isinstance(left, dict):
        if len(left) != len(right):
            return False, False, max_diff

        if set(left.keys()) == set(right.keys()):
            paired_values = ((left[key], right[key]) for key in sorted(left))
        else:
            # SQL aliases are model-controlled and often differ from the golden
            # SQL. Compare columns by result position when column counts match.
            paired_values = zip(left.values(), right.values())

        all_match = True
        all_shape = True
        for left_value, right_value in paired_values:
            match, shape, diff = compare_structured_values(
                left_value,
                right_value,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            all_match = all_match and match
            all_shape = all_shape and shape
            max_diff = max(max_diff, diff)
        return all_match, all_shape, max_diff

    return left == right, True, max_diff


def _compare_parsed_results(
    actual_parsed: Any,
    expected_parsed: Any,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, bool, float]:
    return compare_structured_values(
        actual_parsed,
        expected_parsed,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )


def _pair_result_comparisons(
    actual_items: list[dict[str, Any]],
    expected_items: list[dict[str, Any]],
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, bool, float]:
    """Compare generated and verified result sets with flexible statement pairing."""
    if len(actual_items) == len(expected_items):
        same_result = True
        shape_match = True
        max_numeric_diff = 0.0
        for actual, expected in zip(actual_items, expected_items):
            actual_parsed = parse_verified_comparison_value(actual.get("raw_output"))
            expected_parsed = parse_verified_comparison_value(expected.get("raw_output"))
            match, shape, diff = _compare_parsed_results(
                actual_parsed,
                expected_parsed,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            same_result = same_result and match
            shape_match = shape_match and shape
            max_numeric_diff = max(max_numeric_diff, diff)
        return same_result, shape_match, max_numeric_diff

    if len(actual_items) == 1 and len(expected_items) > 1:
        actual_parsed = parse_verified_comparison_value(actual_items[0].get("raw_output"))
        best_shape = False
        max_numeric_diff = 0.0
        for expected in expected_items:
            expected_parsed = parse_verified_comparison_value(expected.get("raw_output"))
            match, shape, diff = _compare_parsed_results(
                actual_parsed,
                expected_parsed,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            max_numeric_diff = max(max_numeric_diff, diff)
            best_shape = best_shape or shape
            if match:
                return True, shape, diff
        return False, best_shape, max_numeric_diff

    return False, False, 0.0


def parse_verified_sql_by_query(path: Path) -> dict[int, list[str]]:
    """Parse verified SQL file into a query-indexed mapping."""
    if not path.exists():
        raise FileNotFoundError(f"Verified SQL file not found: {path}")

    text = path.read_text(encoding="utf-8")
    header_re = re.compile(r"(?m)^--\s*Q(\d+)\s*:")
    matches = list(header_re.finditer(text))
    by_query: dict[int, list[str]] = {}

    for idx, match in enumerate(matches):
        query_index = int(match.group(1))
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        sql_only_lines = [line for line in block.splitlines() if not line.lstrip().startswith("--")]
        sql_only_block = "\n".join(sql_only_lines).strip()
        if not sql_only_block:
            continue
        stmt_matches = re.findall(r"(?is)\b(?:select|with)\b.*?;", sql_only_block)
        if not stmt_matches:
            continue
        by_query.setdefault(query_index, []).extend(stmt.strip() for stmt in stmt_matches)
    return by_query


def compare_generated_with_verified(
    db_path: Path,
    generated_sql_list: list[str],
    sql_exec: dict[str, Any],
    verified_sql_list: list[str],
    *,
    abs_tolerance: float | None = None,
    rel_tolerance: float | None = None,
) -> dict[str, Any]:
    """Compare generated SQL execution outputs with verified SQL outputs."""
    if abs_tolerance is not None and rel_tolerance is not None:
        abs_tol, rel_tol = abs_tolerance, rel_tolerance
    else:
        from ..utils.utils import get_nutrition_settings

        defaults = get_nutrition_settings(None)
        abs_tol = defaults.eval_abs_tolerance if abs_tolerance is None else abs_tolerance
        rel_tol = defaults.eval_rel_tolerance if rel_tolerance is None else rel_tolerance

    if not verified_sql_list:
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "No verified SQL found for this query index",
            "exact_sql_match": None,
            "same_result": None,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tol, "rel": rel_tol},
            "generated_results": None,
            "verified_results": None,
        }
    if len(verified_sql_list) != 1:
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": f"Expected exactly one verified SQL statement, found {len(verified_sql_list)}",
            "exact_sql_match": None,
            "same_result": None,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tol, "rel": rel_tol},
            "generated_results": None,
            "verified_results": None,
        }

    if not sql_exec.get("ok", False):
        return {
            "checked": True,
            "verdict": "wrong",
            "reason": "Generated SQL execution failed",
            "exact_sql_match": False,
            "same_result": False,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tol, "rel": rel_tol},
            "generated_results": extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": None,
        }

    db = open_sql_database(db_path)
    verified_sql = verified_sql_list[0]
    expected_exec = execute_sql(db=db, sql=verified_sql)
    if not expected_exec.get("ok", False):
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "Verified SQL execution failed unexpectedly",
            "exact_sql_match": None,
            "same_result": None,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tol, "rel": rel_tol},
            "generated_results": extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": [
                {
                    "sql": verified_sql,
                    "ok": expected_exec.get("ok", False),
                    "raw_output": expected_exec.get("raw_output"),
                    "error": expected_exec.get("error"),
                }
            ],
        }

    actual_items = extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list)
    expected_items = [
        {
            "sql": verified_sql,
            "ok": expected_exec.get("ok", False),
            "raw_output": expected_exec.get("raw_output"),
            "error": expected_exec.get("error"),
        }
    ]

    actual_sql_norm = [normalize_sql(x["sql"]) for x in actual_items]
    expected_sql_norm = [normalize_sql(x["sql"]) for x in expected_items]
    exact_sql_match = actual_sql_norm == expected_sql_norm

    same_result, shape_match, max_numeric_diff = _pair_result_comparisons(
        actual_items,
        expected_items,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )

    if len(actual_items) != len(expected_items) and same_result:
        reason = "Matches verified output (equivalent result format)"
    elif len(actual_items) != len(expected_items):
        reason = (
            f"Different SQL statement count: generated={len(actual_items)} "
            f"verified={len(expected_items)}"
        )
    elif not shape_match:
        reason = "Result shape differs from verified SQL output"
    else:
        reason = "Matches verified output" if same_result else "Output differs from verified SQL output"

    return {
        "checked": True,
        "verdict": "right" if same_result else "wrong",
        "reason": reason,
        "exact_sql_match": exact_sql_match,
        "same_result": same_result,
        "shape_match": shape_match,
        "max_numeric_diff": max_numeric_diff,
        "tolerance_used": {"abs": abs_tol, "rel": rel_tol},
        "generated_statement_count": len(actual_items),
        "verified_statement_count": len(expected_items),
        "generated_results": actual_items,
        "verified_results": expected_items,
    }
