from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from dotenv import load_dotenv
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

load_dotenv()


def _build_prompt(table_info: str, sample_rows_text: str, top_k: int) -> str:
    return f"""
You are an expert DuckDB SQL analyst for public health nutrition analytics.
Generate SQL for the `nutrition_data` table only.

Schema context:
{table_info}

Business grounding:
- Month prefixes: `feb24_`, `mar24_`, `apr24_`.
- Use indicator/status columns already present in the table; do not invent derived definitions.
- You have data from feb24, mar24, and apr24, do not answer questions about other months.
- Status column string values (use these exactly, including underscores — never substitute spaces or different capitalization):
  - `{{month}}_underweight_status`: 'normal', 'moderately_underweight', 'severely_underweight'
  - `{{month}}_stunting_status`:    'normal', 'moderately_stunted', 'severely_stunted'
  - `{{month}}_wasting_status`:     'normal', 'overweight', 'obese', 'MAM', 'SAM'
- For district-level filtering, use `WHERE district_name = '<District Name>'`.
  The `district_name` column (VARCHAR) is already in `nutrition_data`; no join is needed.

Hard SQL rules:
1) Return exactly one read-only DuckDB query (`SELECT` or `WITH`).
2) Do not add commentary, markdown fences, or explanations.
3) Use `NULLIF(..., 0)` for all ratio denominators.
4) For prevalence percentages on indicator columns, prefer:
   `AVG(CASE WHEN indicator_col = 1 THEN 1 ELSE 0 END) * 100.0`
   (avoid relying on implicit bool/int casting).
5) For low birth weight cohorts, use `birth_weight > 0 AND birth_weight < 2.5` unless user asks otherwise.
6) For ranked/list outputs, return only fields required by the question (no extra helper columns).
7) If user asks for N rows (e.g., 10 districts), use that exact `LIMIT N`.
8) For single aggregate questions (one metric), do not add unnecessary `LIMIT`.
9) For month-wise status/prevalence outputs, prefer row-wise format with `UNION ALL` and a month label column.
10) For "normal for stunting, underweight, and wasting at the same time", compute the joint condition in one metric.
11) "How many" questions → return `COUNT(*)` (an integer count).
    "What percentage / rate / share" questions → use the percentage formula.
    Never return a rate when the question asks for a count, and vice versa.
12) "Moved from [status_A] to [status_B]" means the child had `month_T_status = 'status_A'`
    in one month AND `month_T+1_status = 'status_B'` in the next month.
    Available adjacent-month transitions: Feb→Mar (feb24→mar24) and Mar→Apr (mar24→apr24).
    When the question says "for Feb, March and April" on a transition query, output BOTH
    transitions as two rows via `UNION ALL` with a transition label column (e.g. 'Feb->Mar',
    'Mar->Apr'). Do NOT treat this as a per-month status snapshot.

Reference patterns:
- Data completeness:
  `(COUNT(*) FILTER (WHERE cond) * 100.0) / NULLIF(COUNT(*), 0)`
- Longitudinal improvement rate (% of starters who improved):
  `(COUNT(*) FILTER (WHERE start_cond AND end_cond) * 100.0) / NULLIF(COUNT(*) FILTER (WHERE start_cond), 0)`
- Longitudinal count — how many children made a transition (single period):
  `SELECT COUNT(*) AS children_count FROM nutrition_data WHERE feb24_X_status = 'A' AND mar24_X_status = 'B'`
- Longitudinal count — district-specific (single period):
  `SELECT COUNT(*) AS children_count FROM nutrition_data WHERE district_name = '<D>' AND feb24_X_status = 'A' AND mar24_X_status = 'B'`
- Longitudinal count — multi-period breakdown (UNION ALL of both transitions):
  ```
  SELECT 'Feb->Mar' AS period, COUNT(*) AS children_count
  FROM nutrition_data
  WHERE district_name = '<D>' AND feb24_X_status = 'A' AND mar24_X_status = 'B'
  UNION ALL
  SELECT 'Mar->Apr' AS period, COUNT(*) AS children_count
  FROM nutrition_data
  WHERE district_name = '<D>' AND mar24_X_status = 'A' AND apr24_X_status = 'B'
  ```

Data sample:
{sample_rows_text}
"""


def _extract_sql(text: str) -> str:
    sql_fence = re.search(r"```sql\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if sql_fence:
        return sql_fence.group(1).strip().rstrip(";") + ";"

    generic_fence = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence and "select" in generic_fence.group(1).lower():
        return generic_fence.group(1).strip().rstrip(";") + ";"

    stmt = re.search(
        r"(?is)\b(select|with)\b.*?(?:;|$)",
        text.strip(),
    )
    if stmt:
        return stmt.group(0).strip().rstrip(";") + ";"
    return text.strip().rstrip(";") + ";"


def _prepare_db_context(db_path: Path) -> tuple[SQLDatabase, str, str]:
    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_info = db.get_table_info(["nutrition_data"])
    sample_rows_text = db.run("SELECT * FROM nutrition_data LIMIT 3")
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return db, table_info, sample_rows_text


@lru_cache(maxsize=4)
def _cached_prompt_context(db_path_str: str) -> tuple[str, str]:
    db = SQLDatabase.from_uri(f"duckdb:///{db_path_str}")
    table_info = db.get_table_info(["nutrition_data"])
    sample_rows_text = db.run("SELECT * FROM nutrition_data LIMIT 3")
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return table_info, sample_rows_text


@lru_cache(maxsize=8)
def _cached_llm(model: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(model=model, temperature=temperature)


def _normalize_sql(sql: str) -> str:
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _generate_sql(
    question: str,
    llm: ChatOpenAI,
    prompt: str,
) -> tuple[str, str]:
    llm_input = f"{prompt}\n\nUser question:\n{question}"
    response = llm.invoke(llm_input)
    raw = str(getattr(response, "content", response))
    sql = _extract_sql(raw)
    return raw, sql


def _is_read_only_sql(sql: str) -> bool:
    normalized = sql.strip().lower().lstrip("(")
    return normalized.startswith("select") or normalized.startswith("with")


def _execute_sql(db: SQLDatabase, sql: str) -> dict[str, Any]:
    if not _is_read_only_sql(sql):
        return {
            "ok": False,
            "raw_output": None,
            "error": "Blocked non-read-only SQL. Only SELECT/WITH statements are allowed.",
        }
    try:
        # Include column names so downstream UIs can render proper headers.
        raw_output = db.run(sql, include_columns=True)
        return {"ok": True, "raw_output": raw_output, "error": None}
    except Exception as exc:
        return {"ok": False, "raw_output": None, "error": str(exc)}


def _execute_sql_list(db: SQLDatabase, sql_list: list[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    all_ok = True
    for sql in sql_list:
        item = _execute_sql(db=db, sql=sql)
        results.append({"sql": sql, **item})
        if not item["ok"]:
            all_ok = False
    return {
        "ok": all_ok,
        "raw_output": [r["raw_output"] for r in results],
        "error": None if all_ok else "; ".join(str(r["error"]) for r in results if r["error"]),
        "results": results,
    }


def _extract_exec_items(sql_exec: dict[str, Any], generated_sql_list: list[str]) -> list[dict[str, Any]]:
    if sql_exec.get("results"):
        return sql_exec["results"]
    return [
        {
            "sql": generated_sql_list[0] if generated_sql_list else "",
            "ok": sql_exec.get("ok", False),
            "raw_output": sql_exec.get("raw_output"),
            "error": sql_exec.get("error"),
        }
    ]


def _parse_raw_output(raw_output: Any) -> Any:
    if raw_output is None:
        return None
    text = str(raw_output).strip()
    if not text:
        return []
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _compare_structured_values(
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
        # Compare list rows deterministically to avoid ordering artifacts.
        left_items = sorted(left, key=repr)
        right_items = sorted(right, key=repr)
        all_match = True
        all_shape = True
        for l_item, r_item in zip(left_items, right_items):
            match, shape, diff = _compare_structured_values(
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
            match, shape, diff = _compare_structured_values(
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
            match, shape, diff = _compare_structured_values(
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


def parse_verified_sql_by_query(path: Path) -> dict[int, list[str]]:
    """Parse verified SQL file into a query-indexed mapping."""
    if not path.exists():
        raise FileNotFoundError(f"Verified SQL file not found: {path}")

    text = path.read_text(encoding="utf-8")
    header_re = re.compile(r"(?m)^--\s*Q(\d+)(?:\s+follow-up)?\s*:")
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
            "generated_results": _extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": None,
        }

    db, _, _ = _prepare_db_context(db_path)
    expected_exec = _execute_sql_list(db=db, sql_list=verified_sql_list)
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
            "generated_results": _extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list),
            "verified_results": expected_exec.get("results"),
        }

    actual_items = _extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list)
    expected_items = expected_exec["results"]

    actual_sql_norm = [_normalize_sql(x["sql"]) for x in actual_items]
    expected_sql_norm = [_normalize_sql(x["sql"]) for x in expected_items]
    exact_sql_match = actual_sql_norm == expected_sql_norm

    shape_match = len(actual_items) == len(expected_items)
    same_result = shape_match
    max_numeric_diff = 0.0
    for actual, expected in zip(actual_items, expected_items):
        actual_parsed = _parse_raw_output(actual.get("raw_output"))
        expected_parsed = _parse_raw_output(expected.get("raw_output"))
        match, shape, diff = _compare_structured_values(
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


def _repair_sql(
    llm: ChatOpenAI,
    broken_sql: str,
    error_text: str,
) -> tuple[str, str]:
    repair_prompt = f"""
Fix the DuckDB SQL query so it executes successfully.
Return only corrected SQL, no explanation.

Broken SQL:
{broken_sql}

Execution error:
{error_text}
"""
    repair_response = llm.invoke(repair_prompt)
    repair_raw = str(getattr(repair_response, "content", repair_response))
    repaired_sql = _extract_sql(repair_raw)
    return repair_raw, repaired_sql


def warmup_runtime(db_path: Path, model: str, temperature: float) -> None:
    """
    Prime cached schema/sample context and LLM client to reduce first-query latency.
    """
    if not db_path.exists():
        return
    _cached_prompt_context(str(db_path.resolve()))
    _cached_llm(model=model, temperature=temperature)


def run_single_question(
    question: str,
    db_path: Path,
    model: str,
    temperature: float,
    top_k: int,
    prefer_verified_templates: bool | None = None,
) -> dict[str, Any]:
    _ = prefer_verified_templates  # Backward-compatible no-op: routing removed.
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set. Export it or add it to .env.")
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}. Build it first with "
            "python scripts/build_nutrition_db.py"
        )

    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_info, sample_rows_text = _cached_prompt_context(str(db_path.resolve()))
    prompt = _build_prompt(table_info=table_info, sample_rows_text=sample_rows_text, top_k=top_k)
    llm = _cached_llm(model=model, temperature=temperature)

    llm_raw_output, sql_query = _generate_sql(question=question, llm=llm, prompt=prompt)
    sql_exec = _execute_sql(db=db, sql=sql_query)
    repaired_sql: str | None = None
    repair_llm_output: str | None = None
    if not sql_exec["ok"]:
        repair_llm_output, repaired_sql = _repair_sql(
            llm=llm,
            broken_sql=sql_query,
            error_text=str(sql_exec["error"]),
        )
        sql_exec = _execute_sql(db=db, sql=repaired_sql)
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
    }


def run_query_dataframe(db_path: Path, sql: str, limit: int = 200) -> pd.DataFrame:
    """Execute SQL directly with DuckDB and return a display dataframe."""
    if not _is_read_only_sql(sql):
        return pd.DataFrame({"error": ["Only SELECT/WITH statements are allowed."]})
    wrapped_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS q LIMIT {limit}"
    # Keep connection mode consistent with other active DuckDB connections.
    # Mixing read_only and default connections on the same DB file can fail.
    with duckdb.connect(str(db_path)) as conn:
        return conn.execute(wrapped_sql).fetchdf()


def read_queries_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Queries file not found: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_batch_outputs(
    output_dir: Path,
    queries_file: Path,
    db_path: Path,
    results: list[dict[str, Any]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"llm_to_sql_trace_{timestamp}.json"
    latest_json = output_dir / "llm_to_sql_trace_latest.json"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "queries_file": str(queries_file),
        "db_path": str(db_path),
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path


def save_single_trace(output_dir: Path, db_path: Path, result: dict[str, Any]) -> Path:
    item = {
        "query_index": 1,
        "query": result["question"],
        "llm_input": result["llm_input"],
        "llm_raw_output": result["llm_raw_output"],
        "generated_sql": result["generated_sql"],
        "generated_sql_list": result.get("generated_sql_list", [result["generated_sql"]]),
        "template_route": result.get("template_route"),
        "repair_llm_output": result["repair_llm_output"],
        "repaired_sql": result["repaired_sql"],
        "sql_execution": result["sql_execution"],
        "comparison": result["comparison"],
    }
    return save_batch_outputs(
        output_dir=output_dir,
        queries_file=Path("__single_question__"),
        db_path=db_path,
        results=[item],
    )
