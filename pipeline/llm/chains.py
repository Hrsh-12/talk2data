"""System prompt text, LangChain chat chains, and SQL generation/repair."""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from pipeline.sql.text import extract_sql


def build_prompt(table_info: str, sample_rows_text: str, top_k: int) -> str:
    """
    Build the system-style instruction block for the LLM.

    top_k is reserved for future use (e.g. embedding a preferred LIMIT in the prompt);
    it is intentionally not interpolated today so CLI/UI behavior stays unchanged.
    """
    _ = top_k
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


SQL_GENERATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "{system_instructions}"),
        ("human", "User question:\n{question}"),
    ]
)

SQL_REPAIR_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            """Fix the DuckDB SQL query so it executes successfully.
Return only corrected SQL, no explanation.

Broken SQL:
{broken_sql}

Execution error:
{error_text}""",
        ),
    ]
)


def _sql_generation_chain(llm: ChatOpenAI) -> Runnable:
    return SQL_GENERATION_PROMPT | llm | StrOutputParser()


def _sql_repair_chain(llm: ChatOpenAI) -> Runnable:
    return SQL_REPAIR_PROMPT | llm | StrOutputParser()


def generate_sql(
    question: str,
    llm: ChatOpenAI,
    prompt: str,
) -> tuple[str, str]:
    """Run the NL→SQL chain; `prompt` is the full system block from build_prompt()."""
    chain = _sql_generation_chain(llm)
    raw = chain.invoke({"system_instructions": prompt, "question": question})
    sql = extract_sql(raw)
    return raw, sql


def repair_sql(
    llm: ChatOpenAI,
    broken_sql: str,
    error_text: str,
) -> tuple[str, str]:
    chain = _sql_repair_chain(llm)
    repair_raw = chain.invoke({"broken_sql": broken_sql, "error_text": error_text})
    repaired_sql = extract_sql(repair_raw)
    return repair_raw, repaired_sql
