from __future__ import annotations

import re


def extract_sql(text: str) -> str:
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
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    return s.lower()
