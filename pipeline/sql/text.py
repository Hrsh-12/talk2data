"""SQL safety, parsing LLM output, and normalization for comparisons."""

from __future__ import annotations

import re


def is_read_only_sql(sql: str) -> bool:
    """Return True if the statement looks like a single SELECT or WITH (read-only)."""
    normalized = sql.strip().lower().lstrip("(")
    return normalized.startswith("select") or normalized.startswith("with")


def extract_sql(text: str) -> str:
    """Pull a SQL statement from markdown fences or raw model output."""
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


def normalize_sql(sql: str) -> str:
    """Collapse whitespace and lower-case for exact-SQL equality checks."""
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    return s.lower()
