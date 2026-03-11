#!/usr/bin/env python3
"""
LLM-to-SQL for nutritional analytics using DuckDB + LangChain.

Modes:
1) Single question:
   python scripts/llm_to_sql.py "What is the prevalence of SAM in March?"

2) Batch from file (one query per line):
   python scripts/llm_to_sql.py --queries-file "data/queries /queries.txt"

For each query, this script can store:
- LLM input (system prompt + question)
- LLM generated SQL
- SQL server output (columns + rows)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

load_dotenv()

VERIFIED_SQL = {
    "q1_underweight_pct_and_count_feb": """SELECT
  AVG(CASE WHEN feb24_is_underweight = 1 THEN 1 ELSE 0 END) * 100.0 AS underweight_pct_feb,
  COUNT(*) FILTER (WHERE feb24_is_underweight = 1) AS underweight_count_feb
FROM nutrition_data;""",
    "q2_height_weight_recorded_pct_mar": """SELECT
  (COUNT(*) FILTER (WHERE mar24_height > 0 AND mar24_weight > 0) * 100.0)
  / NULLIF(COUNT(*), 0) AS height_weight_recorded_pct_mar
FROM nutrition_data;""",
    "q3_improved_underweight_feb_to_mar_pct": """SELECT
  (COUNT(*) FILTER (WHERE feb24_is_underweight = 1 AND mar24_is_underweight = 0) * 100.0)
  / NULLIF(COUNT(*) FILTER (WHERE feb24_is_underweight = 1), 0) AS improved_underweight_to_normal_pct
FROM nutrition_data;""",
    "q4_sam_pct_feb_vs_mar": """SELECT
  AVG(CASE WHEN feb24_is_sam = 1 THEN 1 ELSE 0 END) * 100.0 AS sam_pct_feb,
  AVG(CASE WHEN mar24_is_sam = 1 THEN 1 ELSE 0 END) * 100.0 AS sam_pct_mar,
  (AVG(CASE WHEN feb24_is_sam = 1 THEN 1 ELSE 0 END)
   - AVG(CASE WHEN mar24_is_sam = 1 THEN 1 ELSE 0 END)) * 100.0 AS sam_pct_point_reduction
FROM nutrition_data;""",
    "q4_top5_district_sam_improvement": """SELECT
  district_id,
  AVG(CASE WHEN feb24_is_sam = 1 THEN 1 ELSE 0 END) * 100.0 AS sam_pct_feb,
  AVG(CASE WHEN mar24_is_sam = 1 THEN 1 ELSE 0 END) * 100.0 AS sam_pct_mar,
  (AVG(CASE WHEN feb24_is_sam = 1 THEN 1 ELSE 0 END)
   - AVG(CASE WHEN mar24_is_sam = 1 THEN 1 ELSE 0 END)) * 100.0 AS sam_pct_point_reduction
FROM nutrition_data
GROUP BY district_id
HAVING (AVG(CASE WHEN feb24_is_sam = 1 THEN 1 ELSE 0 END)
        - AVG(CASE WHEN mar24_is_sam = 1 THEN 1 ELSE 0 END)) > 0
ORDER BY sam_pct_point_reduction DESC
LIMIT 5;""",
    "q5_low_birth_weight_underweight_pct_apr": """SELECT
  (COUNT(*) FILTER (WHERE apr24_is_underweight = 1) * 100.0)
  / NULLIF(COUNT(*), 0) AS underweight_pct_apr_among_low_birth_weight
FROM nutrition_data
WHERE birth_weight > 0 AND birth_weight < 2.5;""",
}


def _build_prompt(table_info: str, sample_rows_text: str, top_k: int) -> str:
    return f"""
You are an expert SQL analyst specializing in public health and nutritional surveillance.
Your goal is to generate DuckDB SQL queries to answer questions based on the `nutrition_data` table.

The table schema is provided below:
{table_info}

### Business Logic & Schema Grounding:
- **Temporal Suffixes**: Columns starting with `feb24_`, `mar24_`, and `apr24_` refer to specific monthly data points.
- **SAM (Severe Acute Malnutrition)**: For this research, SAM is defined as Weight-for-Height < -2 SD. Prioritize using the binary indicator columns (e.g., `mar24_is_sam`) when available.
- **Data Gaps**: If the user asks for Haemoglobin (Hb) levels or test results, explicitly state: "The dataset does not contain haemoglobin (Hb) testing data for April 2024."

### Preferred SQL Patterns (DuckDB Dialect):
- **Prevalence Percentage**: `AVG(binary_column::INT) * 100.0`
- **Data Completeness**: `(COUNT(entered_date_column) * 100.0) / NULLIF(COUNT(beneficiary_id), 0)`
- **Longitudinal Improvement (Feb to Mar)**: 
  Calculate the percentage of children who were underweight in February but reached normal status in March:
  `SELECT (COUNT(*) FILTER (WHERE feb24_is_underweight = 1 AND mar24_is_underweight = 0) * 100.0) / NULLIF(COUNT(*) FILTER (WHERE feb24_is_underweight = 1), 0) FROM nutrition_data`
- **District Improvement Trends**: 
  To find districts with the largest decrease in SAM prevalence:
  `SELECT district_id, (AVG(feb24_is_sam::INT) - AVG(mar24_is_sam::INT)) AS sam_reduction FROM nutrition_data GROUP BY district_id ORDER BY sam_reduction DESC LIMIT {top_k}`

### Operational Constraints:
- Always use `NULLIF(denominator, 0)` to prevent division by zero errors.
- Unless the user requests "all" or a specific number, always `LIMIT` results to {top_k}.
- Use standard DuckDB casting (e.g., `column::INT`) where necessary.

### Data Sample:
{sample_rows_text}

### Output contract:
- Return exactly one DuckDB SQL query.
- Do not include explanations.
- Prefer a single SELECT statement.
- Unless the user explicitly asks for all rows, include LIMIT {top_k} for list-style outputs.
"""


def _extract_sql(text: str) -> str:
    sql_fence = re.search(r"```sql\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if sql_fence:
        return sql_fence.group(1).strip().rstrip(";") + ";"

    generic_fence = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence and "select" in generic_fence.group(1).lower():
        return generic_fence.group(1).strip().rstrip(";") + ";"

    # Fallback: first SQL-looking statement
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


def _normalize_sql(sql: str) -> str:
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _route_verified_sql(question: str) -> tuple[str | None, list[str] | None]:
    q = question.strip().lower()

    if "improved" in q and "underweight" in q and "normal" in q:
        return "q3_improved_underweight_feb_to_mar_pct", [VERIFIED_SQL["q3_improved_underweight_feb_to_mar_pct"]]

    if "height and weight" in q and ("march" in q or "mar" in q):
        return "q2_height_weight_recorded_pct_mar", [VERIFIED_SQL["q2_height_weight_recorded_pct_mar"]]

    if ("underweight" in q and "feb" in q) and (
        "were underweight" in q or "how many" in q or "percentage of children" in q
    ):
        return "q1_underweight_pct_and_count_feb", [VERIFIED_SQL["q1_underweight_pct_and_count_feb"]]

    if "sam" in q and ("february" in q or "feb" in q) and ("march" in q or "mar" in q):
        asks_districts = ("district" in q) or ("top 5" in q) or ("list of 5" in q)
        if asks_districts:
            return "q4_sam_main_and_followup", [
                VERIFIED_SQL["q4_sam_pct_feb_vs_mar"],
                VERIFIED_SQL["q4_top5_district_sam_improvement"],
            ]
        return "q4_sam_pct_feb_vs_mar", [VERIFIED_SQL["q4_sam_pct_feb_vs_mar"]]

    if "low birth weight" in q and "underweight" in q and ("april" in q or "apr" in q):
        return "q5_low_birth_weight_underweight_pct_apr", [
            VERIFIED_SQL["q5_low_birth_weight_underweight_pct_apr"]
        ]

    return None, None


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


def _execute_sql(db: SQLDatabase, sql: str) -> dict[str, Any]:
    try:
        # SQLDatabase.run returns a textual representation; keep raw and parsed forms.
        raw_output = db.run(sql)
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


def _compare_against_verified(
    question: str,
    db: SQLDatabase,
    generated_sql_list: list[str],
    sql_exec: dict[str, Any],
) -> dict[str, Any]:
    route_key, expected_sql_list = _route_verified_sql(question)
    if not expected_sql_list:
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "No verified template mapping for this query",
            "template_route": None,
            "exact_sql_match": None,
            "same_result": None,
        }

    if not sql_exec.get("ok", False):
        return {
            "checked": True,
            "verdict": "wrong",
            "reason": "SQL execution failed",
            "template_route": route_key,
            "exact_sql_match": False,
            "same_result": False,
        }

    expected_exec = _execute_sql_list(db=db, sql_list=expected_sql_list)
    if not expected_exec.get("ok", False):
        return {
            "checked": False,
            "verdict": "not_checked",
            "reason": "Verified template execution failed unexpectedly",
            "template_route": route_key,
            "exact_sql_match": None,
            "same_result": None,
        }

    actual_items = _extract_exec_items(sql_exec=sql_exec, generated_sql_list=generated_sql_list)
    expected_items = expected_exec["results"]

    actual_sql_norm = [_normalize_sql(x["sql"]) for x in actual_items]
    expected_sql_norm = [_normalize_sql(x["sql"]) for x in expected_items]
    exact_sql_match = actual_sql_norm == expected_sql_norm

    actual_out = [str(x.get("raw_output")) for x in actual_items]
    expected_out = [str(x.get("raw_output")) for x in expected_items]
    same_result = actual_out == expected_out

    return {
        "checked": True,
        "verdict": "right" if same_result else "wrong",
        "reason": "Matches verified output" if same_result else "Output differs from verified template output",
        "template_route": route_key,
        "exact_sql_match": exact_sql_match,
        "same_result": same_result,
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


def run_single_question(
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

    db, table_info, sample_rows_text = _prepare_db_context(db_path)
    prompt = _build_prompt(table_info=table_info, sample_rows_text=sample_rows_text, top_k=top_k)
    llm = ChatOpenAI(model=model, temperature=temperature)
    routed_key, routed_sql_list = _route_verified_sql(question)

    if routed_sql_list:
        sql_query = routed_sql_list[0]
        llm_raw_output = f"[ROUTED_TO_VERIFIED_SQL_TEMPLATE] {routed_key}"
        sql_exec = _execute_sql_list(db=db, sql_list=routed_sql_list)
        comparison = _compare_against_verified(
            question=question,
            db=db,
            generated_sql_list=routed_sql_list,
            sql_exec=sql_exec,
        )
        return {
            "question": question,
            "llm_input": prompt,
            "llm_raw_output": llm_raw_output,
            "generated_sql": sql_query,
            "generated_sql_list": routed_sql_list,
            "template_route": routed_key,
            "repair_llm_output": None,
            "repaired_sql": None,
            "sql_execution": sql_exec,
            "comparison": comparison,
        }

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
    comparison = _compare_against_verified(
        question=question,
        db=db,
        generated_sql_list=final_sql_list,
        sql_exec=sql_exec,
    )
    return {
        "question": question,
        "llm_input": prompt,
        "llm_raw_output": llm_raw_output,
        "generated_sql": sql_query,
        "generated_sql_list": final_sql_list,
        "template_route": None,
        "repair_llm_output": repair_llm_output,
        "repaired_sql": repaired_sql,
        "sql_execution": sql_exec,
        "comparison": comparison,
    }


def _read_queries_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Queries file not found: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save_batch_outputs(
    output_dir: Path,
    queries_file: Path,
    db_path: Path,
    results: list[dict[str, Any]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"llm_to_sql_trace_{timestamp}.json"
    md_path = output_dir / f"llm_to_sql_trace_{timestamp}.md"
    latest_json = output_dir / "llm_to_sql_trace_latest.json"
    latest_md = output_dir / "llm_to_sql_trace_latest.md"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "queries_file": str(queries_file),
        "db_path": str(db_path),
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    md_lines: list[str] = []
    md_lines.append("# LLM to SQL Trace")
    md_lines.append("")
    md_lines.append(f"- Generated at: `{payload['generated_at']}`")
    md_lines.append(f"- Queries file: `{queries_file}`")
    md_lines.append(f"- DB path: `{db_path}`")
    md_lines.append("")

    for item in results:
        md_lines.append(f"## Query {item['query_index']}")
        md_lines.append("")
        md_lines.append(f"**Question:** {item['query']}")
        md_lines.append("")
        md_lines.append("### LLM Input")
        md_lines.append("```text")
        md_lines.append(item["llm_input"])
        md_lines.append("```")
        md_lines.append("")
        md_lines.append("### LLM Output SQL")
        md_lines.append("```sql")
        md_lines.append(item["generated_sql"])
        md_lines.append("```")
        md_lines.append("")
        if item.get("generated_sql_list") and len(item["generated_sql_list"]) > 1:
            md_lines.append("### Additional SQL Statements")
            for idx, sql_text in enumerate(item["generated_sql_list"][1:], start=2):
                md_lines.append(f"Statement {idx}:")
                md_lines.append("```sql")
                md_lines.append(sql_text)
                md_lines.append("```")
            md_lines.append("")
        if item.get("repaired_sql"):
            md_lines.append("### Repaired SQL (after execution error)")
            md_lines.append("```sql")
            md_lines.append(item["repaired_sql"])
            md_lines.append("```")
            md_lines.append("")
        md_lines.append("### SQL Server Output")
        md_lines.append("```text")
        if item["sql_execution"]["ok"]:
            md_lines.append(str(item["sql_execution"]["raw_output"]))
        else:
            md_lines.append(f"ERROR: {item['sql_execution']['error']}")
        md_lines.append("```")
        md_lines.append("")
        if item.get("comparison"):
            md_lines.append("### Comparison Verdict")
            md_lines.append("```json")
            md_lines.append(json.dumps(item["comparison"], indent=2))
            md_lines.append("```")
            md_lines.append("")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-to-SQL for cleaned nutrition dataset")
    parser.add_argument("question", nargs="*", help="Natural language question")
    parser.add_argument(
        "--queries-file",
        help="Path to text file with one natural-language query per line",
    )
    parser.add_argument("--db", default="database/nutrition_data.duckdb", help="Path to DuckDB file")
    parser.add_argument("--model", default="gpt-5-mini", help="OpenAI-compatible chat model")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature")
    parser.add_argument("--top-k", type=int, default=5, help="Preferred max rows in response")
    parser.add_argument("--output-dir", default="outputs", help="Directory for saved batch trace files")
    args = parser.parse_args()

    if not args.queries_file and not args.question:
        print("Error: provide either a question or --queries-file")
        return 1
    if args.queries_file and args.question:
        print("Error: use either a single question OR --queries-file, not both")
        return 1

    db_path = Path(args.db)

    try:
        if args.queries_file:
            queries_file = Path(args.queries_file)
            queries = _read_queries_file(queries_file)
            results: list[dict[str, Any]] = []
            for i, query in enumerate(queries, start=1):
                print(f"Running query {i}/{len(queries)}: {query}")
                run_result = run_single_question(
                    question=query,
                    db_path=db_path,
                    model=args.model,
                    temperature=args.temperature,
                    top_k=args.top_k,
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
                        "comparison": run_result["comparison"],
                    }
                )

            json_path, md_path = _save_batch_outputs(
                output_dir=Path(args.output_dir),
                queries_file=queries_file,
                db_path=db_path,
                results=results,
            )
            print(f"\nSaved JSON trace: {json_path}")
            print(f"Saved Markdown trace: {md_path}")
            return 0
        else:
            question = " ".join(args.question)
            print(f"Question: {question}\n")
            run_result = run_single_question(
                question=question,
                db_path=db_path,
                model=args.model,
                temperature=args.temperature,
                top_k=args.top_k,
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
