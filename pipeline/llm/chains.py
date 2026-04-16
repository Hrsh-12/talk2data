"""System prompt text, LangChain chat chains, and SQL generation/repair."""

from __future__ import annotations

from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from pipeline.llm.prompt_loader import load_prompt_template
from pipeline.sql.text import extract_sql

DEFAULT_REPAIR_PROMPT_TEXT = """Fix the SQL query so it executes successfully.
Return only corrected SQL, no explanation.

Broken SQL:
{broken_sql}

Execution error:
{error_text}
"""


def build_prompt(
    *,
    prompt_path: Path,
    table_info: str,
    sample_rows_text: str,
    top_k: int,
    schema_notes_block: str = "",
    hints_block: str = "",
) -> str:
    """
    Build the system-style instruction block from a template file.

    top_k is reserved for future use in templates; pass through for callers that
    interpolate it in custom prompt files.
    """
    _ = top_k
    template = load_prompt_template(prompt_path)
    sn = (schema_notes_block.strip() + "\n\n") if schema_notes_block.strip() else ""
    hb = (hints_block.strip() + "\n\n") if hints_block.strip() else ""
    return template.format(
        table_info=table_info,
        sample_rows_text=sample_rows_text,
        schema_notes_block=sn,
        hints_block=hb,
    )


SQL_GENERATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "{system_instructions}"),
        ("human", "{question}"),
    ]
)


def _sql_generation_chain(llm: ChatOpenAI) -> Runnable:
    return SQL_GENERATION_PROMPT | llm | StrOutputParser()


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
    *,
    repair_prompt_path: Path | None = None,
) -> tuple[str, str]:
    if repair_prompt_path is not None and repair_prompt_path.exists():
        tmpl = load_prompt_template(repair_prompt_path)
    else:
        tmpl = DEFAULT_REPAIR_PROMPT_TEXT
    prompt = ChatPromptTemplate.from_messages([("human", tmpl)])
    chain = prompt | llm | StrOutputParser()
    repair_raw = chain.invoke({"broken_sql": broken_sql, "error_text": error_text})
    repaired_sql = extract_sql(repair_raw)
    return repair_raw, repaired_sql
