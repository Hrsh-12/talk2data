"""
result_utils.py — SQL output parsing, result formatting, ground truth rendering,
                  and LLM-based answer rephrasing.
"""
from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd
from langchain_openai import ChatOpenAI

from config import (
    DEFAULT_DB_PATH,
    REPHRASE_MAX_RETRIES,
    REPHRASE_MAX_TOKENS,
    REPHRASE_MODEL,
    REPHRASE_TEMPERATURE,
    REPHRASE_TIMEOUT_SECONDS,
)


# ── Low-level formatters ────────────────────────────────────────────────

def _format_scalar(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "null"
    return str(value)


def _to_markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    separator  = "| " + " | ".join(["---"] * len(headers)) + " |"
    body_rows  = [
        "| " + " | ".join(_format_scalar(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header_row, separator, *body_rows])


def _df_to_markdown_table(df: pd.DataFrame) -> str:
    headers = [str(c) for c in df.columns]
    rows = [
        [_format_scalar(cell) for cell in row]
        for row in df.itertuples(index=False)
    ]
    return _to_markdown_table(headers=headers, rows=rows)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


# ── Ground truth loader & renderer ─────────────────────────────────────

def _format_ground_truth_metric(key: str, value: object) -> str:
    key_l = key.lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = _format_scalar(value)
        if "pct" in key_l or "percent" in key_l or "percentage" in key_l:
            return f"{num}%"
        return num
    return _format_scalar(value)


def _render_ground_truth_dict(item: dict[object, object]) -> str:
    return "<br>".join(
        f"{_escape_html(str(k))}: <strong>{_escape_html(_format_ground_truth_metric(str(k), v))}</strong>"
        for k, v in item.items()
    )


def _format_ground_truth_answer(answer_text: str, max_items: int = 5) -> str:
    compact = answer_text.strip()
    if not compact:
        return "<em>No result found in verified file comments.</em>"

    parsed: object = compact
    try:
        parsed = json.loads(compact)
    except Exception:
        try:
            parsed = ast.literal_eval(compact)
        except Exception:
            return f"<code>{_escape_html(compact)}</code>"

    if isinstance(parsed, list):
        if not parsed:
            return "<code>[]</code>"
        if all(isinstance(item, dict) for item in parsed):
            if len(parsed) == 1:
                return _render_ground_truth_dict(parsed[0])
            shown = parsed[:max_items]
            lines: list[str] = []
            for idx, item in enumerate(shown, start=1):
                pairs = ", ".join(
                    f"{_escape_html(str(k))}: <strong>{_escape_html(_format_ground_truth_metric(str(k), v))}</strong>"
                    for k, v in item.items()
                )
                lines.append(f"{idx}. {pairs}")
            suffix = (
                f"<br><em>Showing first {max_items} of {len(parsed)} rows.</em>"
                if len(parsed) > max_items else ""
            )
            return "<br>".join(lines) + suffix
        return f"<code>{_escape_html(str(parsed))}</code>"

    if isinstance(parsed, dict):
        return _render_ground_truth_dict(parsed)

    return f"<strong>{_escape_html(_format_scalar(parsed))}</strong>"


def _load_ground_truth_rows(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return [("N/A", f"Verified file not found: {path}")]

    text = path.read_text(encoding="utf-8")
    q_header_re = re.compile(r"(?m)^--\s*(Q\d+(?:\s+follow-up)?):\s*(.+)$")
    matches = list(q_header_re.finditer(text))
    if not matches:
        return [("N/A", "No query blocks found in verified SQL file.")]

    rows: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        q_label = match.group(1).strip()
        q_text  = match.group(2).strip()
        start   = match.end()
        end     = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block   = text[start:end]

        result_match = re.compile(
            r"(?ms)^--\s*Result[^\n]*\n((?:--[^\n]*\n?)*)"
        ).search(block)

        if result_match:
            comment_lines = [
                raw.strip()[2:].strip()
                for raw in result_match.group(1).splitlines()
                if raw.strip().startswith("--") and raw.strip()[2:].strip()
            ]
            result_text = "".join(comment_lines).strip()
        else:
            result_text = ""

        rows.append((f"{q_label}: {q_text}", result_text or "No result found in verified file comments."))
    return rows


def ground_truth_html(path: Path) -> str:
    """Scrollable HTML table for the ground truth panel."""
    rows = _load_ground_truth_rows(path)
    table_rows = "".join(
        "<tr>"
        f"<td class='gt-query'>{_escape_html(query)}</td>"
        f"<td class='gt-answer'>{_format_ground_truth_answer(answer)}</td>"
        "</tr>"
        for query, answer in rows
    )
    return (
        "<style>"
        ".gt-wrap{overflow-x:auto;overflow-y:auto;max-height:480px}"
        ".gt-tbl{width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px}"
        ".gt-tbl th,.gt-tbl td{border:1px solid #dbeafe;padding:7px 9px;vertical-align:top;text-align:left}"
        ".gt-tbl thead th{background:#eff6ff;font-weight:600;color:#1d4ed8;position:sticky;top:0;z-index:1}"
        ".gt-tbl .gt-query{width:40%;word-break:break-word;color:#374151}"
        ".gt-tbl .gt-answer{width:60%;word-break:break-word;line-height:1.45;color:#374151}"
        ".gt-tbl .gt-answer code{white-space:pre-wrap;word-break:break-word;font-size:11px}"
        ".gt-tbl tr:nth-child(even) td{background:#f8faff}"
        "</style>"
        "<div class='gt-wrap'>"
        "<table class='gt-tbl'>"
        "<thead><tr><th class='gt-query'>Query</th><th class='gt-answer'>Ground Truth</th></tr></thead>"
        f"<tbody>{table_rows}</tbody>"
        "</table></div>"
    )


# ── SQL output parsing ──────────────────────────────────────────────────

def _parse_exec_output(raw_output: object) -> tuple[str, object]:
    if isinstance(raw_output, (list, dict, tuple)):
        return str(raw_output).strip(), raw_output

    text = str(raw_output).strip()
    if not text or text == "None":
        return "", ""
    try:
        return text, ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text, text


def _is_read_only_sql(sql: str) -> bool:
    normalized = sql.strip().lower().lstrip("(")
    return normalized.startswith("select") or normalized.startswith("with")


def _columns_for_sql(db_path: Path, sql: str) -> list[str] | None:
    cleaned = sql.strip()
    if not cleaned or not _is_read_only_sql(cleaned):
        return None
    wrapped = f"SELECT * FROM ({cleaned.rstrip(';')}) AS q LIMIT 0;"
    try:
        with duckdb.connect(str(db_path)) as conn:
            df0 = conn.execute(wrapped).fetchdf()
        return [str(c) for c in df0.columns]
    except Exception:
        return None


# ── Result table builder ────────────────────────────────────────────────

def build_result_table(
    *,
    executed_sql: str,
    exec_payload: dict,
    max_rows: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[pd.DataFrame, str]:
    """
    Convert already-executed SQL output into a display DataFrame + summary markdown.
    Uses a LIMIT 0 probe to recover column names when needed.
    """
    safe_limit = max(1, int(max_rows))

    if not exec_payload.get("ok"):
        err = str(exec_payload.get("error") or "Unknown SQL error.")
        return pd.DataFrame({"error": [err]}), f"### Result\n\n`ERROR:` {err}"

    text, parsed = _parse_exec_output(exec_payload.get("raw_output"))
    if not text:
        return pd.DataFrame(), "### Result\n\nNo rows returned."

    total_rows: int | None = len(parsed) if isinstance(parsed, list) else None

    df: pd.DataFrame
    if isinstance(parsed, list) and parsed and all(isinstance(r, dict) for r in parsed):
        df = pd.DataFrame(parsed[:safe_limit])
    elif isinstance(parsed, list) and parsed and all(isinstance(r, tuple) for r in parsed):
        shown = parsed[:safe_limit]
        col_count = max(len(r) for r in shown)
        normalized = [list(r) + [None] * (col_count - len(r)) for r in shown]
        cols = _columns_for_sql(db_path, executed_sql) if executed_sql else None
        df = pd.DataFrame(
            normalized,
            columns=cols if cols and len(cols) == col_count else [f"col_{i}" for i in range(1, col_count + 1)],
        )
    else:
        df = pd.DataFrame({"value": [parsed]})

    shown_n, cols_n = int(df.shape[0]), int(df.shape[1])
    rows_line = (
        f"**Rows**: {total_rows} (showing first {shown_n})"
        if total_rows is not None and total_rows > shown_n
        else f"**Rows**: {total_rows if total_rows is not None else shown_n}"
    )
    summary_lines = [
        "### Result", "",
        f"{rows_line}  \n**Columns**: {cols_n}",
        f"_Table shows up to {safe_limit} row(s)._",
    ]

    if df.shape == (1, 1):
        cell = df.iloc[0, 0]
        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
            summary_lines.insert(2, f"## {_format_scalar(cell)}")

    return df, "\n".join(summary_lines)


# ── LLM rephraser ───────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _chat_llm(
    model: str,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
    max_retries: int,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )


def _build_rephrase_payload(exec_payload: dict, max_rows: int = 20) -> dict:
    text, parsed = _parse_exec_output(exec_payload.get("raw_output"))
    if not text:
        return {"status": "empty", "preview": []}

    preview: object = parsed
    if isinstance(parsed, list):
        preview = parsed[:max_rows]
    elif isinstance(text, str) and len(text) > 1500:
        preview = text[:1500] + "..."

    return {
        "status": "ok",
        "row_count": len(parsed) if isinstance(parsed, list) else None,
        "is_scalar": (
            isinstance(parsed, list)
            and len(parsed) == 1
            and isinstance(parsed[0], tuple)
            and len(parsed[0]) == 1
        ),
        "preview": preview,
    }


def _fallback_reply(question: str, exec_payload: dict) -> str:
    _ = question
    if not exec_payload.get("ok"):
        err = str(exec_payload.get("error") or "Unknown SQL error.")
        return f"I could not complete that query because SQL execution failed: {err}"

    text, parsed = _parse_exec_output(exec_payload.get("raw_output"))
    if not text:
        return "I ran the SQL successfully, but it returned no rows for your question."
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], tuple) and len(parsed[0]) == 1:
        return f"The result is {_format_scalar(parsed[0][0])}."
    if isinstance(parsed, list):
        return f"I ran the SQL successfully and found {len(parsed)} row(s)."
    compact = text[:220] + ("..." if len(text) > 220 else "")
    return f"I ran the SQL successfully. Result: {compact}"


def rephrase_reply(question: str, exec_payload: dict) -> str:
    """Return a 1-2 sentence natural-language answer rephrased by the LLM."""
    if not exec_payload.get("ok"):
        return _fallback_reply(question, exec_payload)

    payload = _build_rephrase_payload(exec_payload)
    if payload["status"] == "empty":
        return _fallback_reply(question, exec_payload)

    prompt = (
        "You are a data assistant. Rewrite SQL results as a concise direct answer.\n"
        "Rules:\n"
        "1) Use only the provided result payload. Never invent fields, values, or causes.\n"
        "2) Keep output to 1-2 short sentences in plain English.\n"
        "3) Do not include markdown, SQL, or bullet points.\n"
        "4) If the payload is scalar, provide the numeric answer clearly. With at most 2 decimal places.\n"
        "5) If the payload has rows, summarize key values in sentence form.\n"
        "6) If unsure, state limitation briefly and stay factual.\n\n"
        f"User question:\n{question}\n\n"
        "Result payload (JSON):\n"
        f"{json.dumps(payload, ensure_ascii=True, default=str)}\n"
    )
    try:
        llm = _chat_llm(
            model=REPHRASE_MODEL,
            temperature=REPHRASE_TEMPERATURE,
            max_tokens=REPHRASE_MAX_TOKENS,
            timeout_seconds=REPHRASE_TIMEOUT_SECONDS,
            max_retries=REPHRASE_MAX_RETRIES,
        )
        content = str(getattr(llm.invoke(prompt), "content", "")).strip()
        return " ".join(content.split()) if content else _fallback_reply(question, exec_payload)
    except Exception:
        return _fallback_reply(question, exec_payload)
