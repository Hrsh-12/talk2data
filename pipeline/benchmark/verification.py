"""Benchmarking: parse verified SQL files and compare execution outputs."""

from __future__ import annotations

from typing import Any

from pipeline.db.engine import (
    execute_sql_list,
    extract_exec_items,
    parse_raw_output,
    prepare_db_context,
)
from pipeline.sql.text import normalize_sql


def compare_structured_values(
    left: Any,
    right: Any,
    *,
    abs_tol: float,
    rel_tol: float,
) -> tuple[bool, bool, float]:
    """
    Compare parsed SQL outputs recursively.

    Returns: (values_match, shape_match, max_numeric_diff)
    """
    max_diff = 0.0

    is_num_left = isinstance(left, (int, float)) and not isinstance(left, bool)
    is_num_right = isinstance(right, (int, float)) and not isinstance(right, bool)
    if is_num_left and is_num_right:
        diff = abs(float(left) - float(right))
        allowed = max(abs_tol, rel_tol * max(abs(float(left)), abs(float(right)), 1.0))
        return diff <= allowed, True, diff

    if type(left) is not type(right):
        return False, False, max_diff

    if isinstance(left, list):
        if len(left) != len(right):
            return False, False, max_diff
        left_items = sorted(left, key=repr)
        right_items = sorted(right, key=repr)
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
        if set(left.keys()) != set(right.keys()):
            return False, False, max_diff
        all_match = True
        all_shape = True
        for key in sorted(left):
            match, shape, diff = compare_structured_values(
                left[key],
                right[key],
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            all_match = all_match and match
            all_shape = all_shape and shape
            max_diff = max(max_diff, diff)
        return all_match, all_shape, max_diff

    return left == right, True, max_diff


def compare_generated_with_verified(
    sqlalchemy_uri: str,
    schema_tables: list[str] | tuple[str, ...],
    sample_sql: str | None,
    generated_sql_list: list[str],
    sql_exec: dict[str, Any],
    verified_sql_list: list[str],
) -> dict[str, Any]:
    """Compare generated SQL execution outputs with verified SQL outputs."""
    abs_tolerance = 1e-9
    rel_tolerance = 1e-9

    if not verified_sql_list:
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "No verified SQL found for this query index",
            "exact_sql_match": None,
            "same_result": None,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tolerance, "rel": rel_tolerance},
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
            "tolerance_used": {"abs": abs_tolerance, "rel": rel_tolerance},
            "generated_results": extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": None,
        }

    db, _, _ = prepare_db_context(sqlalchemy_uri, list(schema_tables), sample_sql)
    expected_exec = execute_sql_list(db=db, sql_list=verified_sql_list)
    if not expected_exec.get("ok", False):
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "Verified SQL execution failed unexpectedly",
            "exact_sql_match": None,
            "same_result": None,
            "shape_match": None,
            "max_numeric_diff": None,
            "tolerance_used": {"abs": abs_tolerance, "rel": rel_tolerance},
            "generated_results": extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": expected_exec.get("results"),
        }

    actual_items = extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list)
    expected_items = expected_exec["results"]

    actual_sql_norm = [normalize_sql(x["sql"]) for x in actual_items]
    expected_sql_norm = [normalize_sql(x["sql"]) for x in expected_items]
    exact_sql_match = actual_sql_norm == expected_sql_norm

    shape_match = len(actual_items) == len(expected_items)
    same_result = shape_match
    max_numeric_diff = 0.0
    for actual, expected in zip(actual_items, expected_items):
        actual_parsed = parse_raw_output(actual.get("raw_output"))
        expected_parsed = parse_raw_output(expected.get("raw_output"))
        match, shape, diff = compare_structured_values(
            actual_parsed,
            expected_parsed,
            abs_tol=abs_tolerance,
            rel_tol=rel_tolerance,
        )
        same_result = same_result and match
        shape_match = shape_match and shape
        max_numeric_diff = max(max_numeric_diff, diff)

    if len(actual_items) != len(expected_items):
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
        "tolerance_used": {"abs": abs_tolerance, "rel": rel_tolerance},
        "generated_statement_count": len(actual_items),
        "verified_statement_count": len(expected_items),
        "generated_results": actual_items,
        "verified_results": expected_items,
    }
