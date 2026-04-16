"""Regression tests for pure helpers (no DB, no API calls)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.sql.text import extract_sql, normalize_sql
from pipeline.benchmark.verification import compare_structured_values
from pipeline.llm.prompt_loader import load_prompt_template
from pipeline.queries.catalog import load_verified_sql_by_question_id, parse_verified_sql_by_query


def test_extract_sql_from_markdown_fence() -> None:
    raw = 'Here:\n```sql\nSELECT 1 AS x;\n```'
    assert extract_sql(raw).strip().upper().startswith("SELECT")


def test_normalize_sql_collapses_whitespace() -> None:
    # Preserves legacy behavior: internal runs of spaces collapse to one; trailing space may remain.
    assert normalize_sql("  SELECT  1  ;  ") == "select 1 "


@pytest.mark.parametrize(
    "left,right,expect_match",
    [
        (1, 1, True),
        (1.0, 1.0000000001, True),
        ([(1, 2)], [(1, 2)], True),
        ({"a": 1}, {"a": 2}, False),
    ],
)
def test_compare_structured_values(left, right, expect_match: bool) -> None:
    m, _, _ = compare_structured_values(left, right, abs_tol=1e-9, rel_tol=1e-9)
    assert m is expect_match


def test_load_prompt_template_yaml(tmp_path: Path) -> None:
    p = tmp_path / "p.yaml"
    p.write_text(
        'meta:\n  description: test\n'
        'template: "Hello {name}"\n',
        encoding="utf-8",
    )
    assert load_prompt_template(p).format(name="world") == "Hello world"


def test_load_verified_sql_by_question_id_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "q.jsonl"
    p.write_text(
        '{"question_id":1,"db_id":"x","question":"hi","evidence":"NA","SQL":"SELECT 1;","difficulty":"NA"}\n',
        encoding="utf-8",
    )
    by_q = load_verified_sql_by_question_id(p)
    assert 1 in by_q and "SELECT 1" in by_q[1][0].upper()


def test_parse_verified_sql_by_query(tmp_path: Path) -> None:
    p = tmp_path / "v.sql"
    p.write_text(
        """-- Q1: first
-- noise
SELECT 1;
-- Q2: second
WITH t AS (SELECT 2) SELECT * FROM t;
""",
        encoding="utf-8",
    )
    by_q = parse_verified_sql_by_query(p)
    assert 1 in by_q and "SELECT 1" in by_q[1][0].upper()
    assert 2 in by_q and "WITH" in by_q[2][0].upper()
