"""BIRD-style JSON(L) query catalogs and legacy verified-SQL parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def split_sql_statements(sql_block: str) -> list[str]:
    """Extract SELECT/WITH statements (semicolon-terminated) from a SQL block."""
    sql_only_lines = [line for line in sql_block.splitlines() if not line.lstrip().startswith("--")]
    sql_only_block = "\n".join(sql_only_lines).strip()
    if not sql_only_block:
        return []
    stmt_matches = re.findall(r"(?is)\b(?:select|with)\b.*?;", sql_only_block)
    return [stmt.strip() for stmt in stmt_matches]


def _load_jsonl_or_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Query catalog not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array in {path}")
        return [x for x in data if isinstance(x, dict)]
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def load_query_catalog(path: Path) -> list[dict[str, Any]]:
    """Load BIRD-style records: question_id, db_id, question, evidence, SQL, difficulty."""
    return _load_jsonl_or_array(path)


def load_verified_sql_by_question_id(path: Path) -> dict[int, list[str]]:
    """
    Map question_id -> list of gold SQL strings (multiple rows merge like legacy SQL parser).

    Supports `.json`, `.jsonl`, or legacy `.sql` (`-- Qn:` blocks).
    """
    suffix = path.suffix.lower()
    if suffix == ".sql":
        return parse_verified_sql_by_query(path)

    rows = _load_jsonl_or_array(path)
    by_q: dict[int, list[str]] = {}
    for row in rows:
        qid = row.get("question_id")
        if qid is None:
            continue
        qid_i = int(qid)
        sql_raw = row.get("SQL") or row.get("sql") or ""
        if not isinstance(sql_raw, str) or not sql_raw.strip():
            continue
        stmts = split_sql_statements(sql_raw)
        if not stmts:
            stmts = [sql_raw.strip().rstrip(";") + ";"]
        by_q.setdefault(qid_i, []).extend(stmts)
    return by_q


def parse_verified_sql_by_query(path: Path) -> dict[int, list[str]]:
    """Parse legacy verified SQL file (`-- Qn:` headers) into question_id -> SQL list."""
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
        stmts = split_sql_statements(block)
        if stmts:
            by_query.setdefault(query_index, []).extend(stmts)
    return by_query
