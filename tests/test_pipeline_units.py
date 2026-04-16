"""Pure and local integration tests (no live OpenAI; temp DuckDB with generic schema)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import gradio as gr
import pytest
from langchain_community.utilities import SQLDatabase

REPO = Path(__file__).resolve().parents[1]

from pipeline.benchmark.verification import (  # noqa: E402
    compare_generated_with_verified,
    compare_structured_values,
    parse_verified_sql_by_query,
)
from pipeline.db.engine import (  # noqa: E402
    execute_sql,
    extract_exec_items,
    parse_raw_output,
    warmup_runtime,
)
from pipeline.execution.runner import run_query_dataframe, run_single_question  # noqa: E402
from pipeline.paths import resolve_config_path  # noqa: E402
from pipeline.sql.text import extract_sql, is_read_only_sql  # noqa: E402


@pytest.fixture()
def tiny_duckdb(tmp_path: Path) -> Path:
    """Minimal DuckDB with a single arbitrary table (no domain-specific schema)."""
    p = tmp_path / "app.duckdb"
    conn = duckdb.connect(str(p))
    conn.execute("CREATE TABLE t (id INTEGER, name VARCHAR); INSERT INTO t VALUES (1, 'a'), (2, 'b');")
    conn.close()
    return p


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT 1", True),
        ("WITH t AS (SELECT 1) SELECT * FROM t", True),
        ("  select 1", True),
        ("(SELECT 1)", True),
        ("INSERT INTO t VALUES (1)", False),
        ("DELETE FROM t", False),
        ("UPDATE t SET x=1", False),
        ("SeLeCt 1", True),
    ],
)
def test_is_read_only_sql(sql: str, expected: bool) -> None:
    assert is_read_only_sql(sql) is expected


def test_extract_sql_no_select_appends_semicolon() -> None:
    out = extract_sql("Just some prose without SQL.")
    assert out.endswith(";")


def test_execute_sql_blocks_insert(tiny_duckdb: Path) -> None:
    db = SQLDatabase.from_uri(f"duckdb:///{tiny_duckdb}")
    r = execute_sql(db, "INSERT INTO t VALUES (3, 'c');")
    assert r["ok"] is False
    assert "read-only" in (r.get("error") or "").lower()


def test_execute_sql_select_ok(tiny_duckdb: Path) -> None:
    db = SQLDatabase.from_uri(f"duckdb:///{tiny_duckdb}")
    r = execute_sql(db, "SELECT COUNT(*) AS c FROM t;")
    assert r["ok"] is True
    assert r["raw_output"] is not None


def test_execute_sql_syntax_error(tiny_duckdb: Path) -> None:
    db = SQLDatabase.from_uri(f"duckdb:///{tiny_duckdb}")
    r = execute_sql(db, "SELECT FROM t WHERE;")
    assert r["ok"] is False
    assert r["error"]


def test_run_query_dataframe_rejects_mutating(tiny_duckdb: Path) -> None:
    df = run_query_dataframe(tiny_duckdb, "DELETE FROM t;", limit=10)
    assert "error" in df.columns


def test_run_query_dataframe_limit(tiny_duckdb: Path) -> None:
    df = run_query_dataframe(tiny_duckdb, "SELECT id FROM t ORDER BY id", limit=1)
    assert len(df) == 1


def test_warmup_runtime_missing_db(tmp_path: Path) -> None:
    missing = tmp_path / "nope.duckdb"
    warmup_runtime(db_path=missing, model="gpt-4o-mini", temperature=0.0)


def test_parse_raw_output_none() -> None:
    assert parse_raw_output(None) is None


def test_extract_exec_items_single() -> None:
    payload = {"ok": True, "raw_output": "[(1,)]", "error": None}
    items = extract_exec_items(sql_exec=payload, generated_sql_list=["SELECT 1"])
    assert len(items) == 1
    assert items[0]["sql"] == "SELECT 1"


def test_run_single_question_missing_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-not-called")
    missing = tmp_path / "missing.duckdb"
    with pytest.raises(FileNotFoundError, match="Database not found"):
        run_single_question(
            question="x",
            db_path=missing,
            model="gpt-4o-mini",
            temperature=0.0,
            top_k=5,
        )


def test_compare_structured_values_list_length_mismatch() -> None:
    m, shape, _ = compare_structured_values([1, 2], [1], abs_tol=1e-9, rel_tol=1e-9)
    assert m is False and shape is False


def test_compare_structured_values_tuple_match() -> None:
    m, shape, _ = compare_structured_values((1, 2), (1, 2), abs_tol=1e-9, rel_tol=1e-9)
    assert m is True and shape is True


def test_parse_verified_sql_by_query_no_headers(tmp_path: Path) -> None:
    p = tmp_path / "x.sql"
    p.write_text("SELECT 1;\n", encoding="utf-8")
    assert parse_verified_sql_by_query(p) == {}


def test_compare_generated_with_verified_empty_verified(tmp_path: Path) -> None:
    """Early return path — db_path is not opened."""
    dummy = tmp_path / "unused.duckdb"
    out = compare_generated_with_verified(
        dummy,
        ["SELECT 1"],
        {"ok": True, "raw_output": "[(1,)]", "error": None},
        [],
    )
    assert out["verdict"] == "not_checked"


def test_compare_generated_with_verified_failed_exec(tmp_path: Path) -> None:
    """Failed generated SQL — no verified execution against db_path."""
    dummy = tmp_path / "unused.duckdb"
    out = compare_generated_with_verified(
        dummy,
        ["SELECT 1"],
        {"ok": False, "raw_output": None, "error": "boom"},
        ["SELECT 1;"],
    )
    assert out["verdict"] == "wrong"


def test_resolve_config_path_relative() -> None:
    orig = Path("/repo")
    assert resolve_config_path("database/x.duckdb", orig) == Path("/repo/database/x.duckdb")


def test_resolve_config_path_absolute() -> None:
    orig = Path("/repo")
    assert resolve_config_path("/abs/x.duckdb", orig) == Path("/abs/x.duckdb")


def test_build_app_returns_blocks(tmp_path: Path, tiny_duckdb: Path) -> None:
    apps_dir = REPO / "apps"
    if str(apps_dir) not in sys.path:
        sys.path.insert(0, str(apps_dir))
    from gradio_app import build_app  # noqa: WPS433

    verified = tmp_path / "v.sql"
    verified.write_text("-- Q1: Example\nSELECT 1;\n", encoding="utf-8")
    out_dir = tmp_path / "traces"
    out_dir.mkdir()
    llm = MagicMock()
    llm.model_name = "gpt-4o-mini"
    demo = build_app(
        db_path=tiny_duckdb,
        verified_sql_path=verified,
        output_dir=out_dir,
        llm=llm,
        merged_model="gpt-4o-mini",
        merged_temp=0.0,
        merged_top_k=5,
        save_trace=False,
        save_history=False,
        rephrase_kw={
            "model": "gpt-4o-mini",
            "temperature": 0.0,
            "max_tokens": 96,
            "timeout_seconds": 20.0,
            "max_retries": 1,
        },
        table_row_limit=10,
    )
    assert isinstance(demo, gr.Blocks)


def test_build_rephrase_payload_scalar() -> None:
    sys.path.insert(0, str(REPO / "apps"))
    from result_utils import _build_rephrase_payload  # noqa: WPS433

    payload = _build_rephrase_payload({"ok": True, "raw_output": "[(42,)]"})
    assert payload["status"] == "ok"
    assert payload.get("is_scalar") is True


def test_build_result_table_sql_error(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "apps"))
    from result_utils import build_result_table  # noqa: WPS433

    df, md = build_result_table(
        executed_sql="SELECT 1",
        exec_payload={"ok": False, "error": "syntax", "raw_output": None},
        max_rows=10,
        db_path=tmp_path / "not_opened.duckdb",
    )
    assert "error" in df.columns
    assert "ERROR" in md


def test_rephrase_reply_falls_back_on_llm_error() -> None:
    apps_dir = str(REPO / "apps")
    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    import result_utils as result_utils_mod  # noqa: WPS433

    with patch.object(result_utils_mod, "_chat_llm", side_effect=RuntimeError("down")):
        text = result_utils_mod.rephrase_reply(
            "What is the count?",
            {"ok": True, "raw_output": "[(7,)]"},
            model="gpt-4o-mini",
            temperature=0.0,
        )
    assert "7" in text or "result" in text.lower()


