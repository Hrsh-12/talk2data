#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.nutrition_sql.service import run_single_question, save_single_trace, warmup_runtime

load_dotenv()

DEFAULT_DB_PATH = Path(os.getenv("DB_PATH", "database/nutrition_data.duckdb"))
VERIFIED_SQL_PATH = ROOT / "data" / "queries " / "queries_verified.sql"
DEFAULT_MODEL = os.getenv("MODEL_NAME", "gpt-5-mini")
DEFAULT_TOP_K = int(os.getenv("TOP_K", "5"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))
REPHRASE_MODEL = os.getenv("REPHRASE_MODEL_NAME", "gpt-4o-mini")
REPHRASE_TEMPERATURE = float(os.getenv("REPHRASE_TEMPERATURE", "0.0"))
REPHRASE_MAX_TOKENS = int(os.getenv("REPHRASE_MAX_TOKENS", "96"))
REPHRASE_TIMEOUT_SECONDS = float(os.getenv("REPHRASE_TIMEOUT_SECONDS", "20"))
REPHRASE_MAX_RETRIES = int(os.getenv("REPHRASE_MAX_RETRIES", "1"))
DEFAULT_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
SAVE_TRACE = os.getenv("SAVE_TRACE", "true").lower() == "true"
SAVE_HISTORY = os.getenv("GRADIO_SAVE_HISTORY", "true").lower() == "true"
QUEUE_CONCURRENCY = int(os.getenv("GRADIO_QUEUE_CONCURRENCY", "2"))
QUEUE_MAX_SIZE = int(os.getenv("GRADIO_QUEUE_MAX_SIZE", "32"))
WARMUP_ON_START = os.getenv("WARMUP_ON_START", "true").lower() == "true"


def _format_scalar(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "null"
    return str(value)


def _to_markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body_rows = [
        "| " + " | ".join(_format_scalar(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header_row, separator, *body_rows])


def _escape_md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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
                pairs = ", ".join([
                    f"{_escape_html(str(k))}: <strong>{_escape_html(_format_ground_truth_metric(str(k), v))}</strong>"
                    for k, v in item.items()
                ])
                lines.append(f"{idx}. {pairs}")
            suffix = f"<br><em>Showing first {max_items} of {len(parsed)} rows.</em>" if len(parsed) > max_items else ""
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
        q_text = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]

        result_comment_re = re.compile(
            r"(?ms)^--\s*Result[^\n]*\n((?:--[^\n]*\n?)*)"
        )
        result_match = result_comment_re.search(block)
        if result_match:
            comment_lines = []
            for raw in result_match.group(1).splitlines():
                stripped = raw.strip()
                if stripped.startswith("--"):
                    content = stripped[2:].strip()
                    if content:
                        comment_lines.append(content)
            result_text = "".join(comment_lines).strip()
        else:
            result_text = ""

        question = f"{q_label}: {q_text}"
        rows.append((question, result_text or "No result found in verified file comments."))
    return rows


def _ground_truth_markdown(path: Path) -> str:
    rows = _load_ground_truth_rows(path)
    table_rows = []
    for query, answer in rows:
        table_rows.append(
            "<tr>"
            f"<td class='gt-query'>{_escape_html(query)}</td>"
            f"<td class='gt-answer'>{_format_ground_truth_answer(answer)}</td>"
            "</tr>"
        )

    return (
        "## Query Ground Truth Reference\n"
        "_Source: `data/queries /queries_verified.sql`_\n\n"
        "<div class='gt-wrap'>"
        "<style>"
        ".gt-wrap { overflow-x: auto; }"
        ".gt-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px; }"
        ".gt-table th, .gt-table td { border: 1px solid #d1d5db; padding: 10px; vertical-align: top; text-align: left; }"
        ".gt-table th { background: #f5f5f5; font-weight: 600; }"
        ".gt-query { width: 38%; white-space: normal; word-break: normal; overflow-wrap: anywhere; }"
        ".gt-answer { width: 62%; white-space: normal; word-break: normal; overflow-wrap: anywhere; line-height: 1.45; }"
        ".gt-answer code { white-space: pre-wrap; word-break: break-word; }"
        "</style>"
        "<table class='gt-table'>"
        "<thead><tr><th>Query</th><th>Ground Truth Answer</th></tr></thead>"
        "<tbody>"
        f"{''.join(table_rows)}"
        "</tbody></table></div>"
    )


def _render_formatted_result(exec_payload: dict) -> str:
    if not exec_payload.get("ok"):
        return f"### Result\n\n`ERROR:` {exec_payload.get('error')}"

    raw_output = exec_payload.get("raw_output")
    text = str(raw_output).strip()
    if not text:
        return "### Result\n\nNo rows returned."

    parsed: object = text
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        parsed = text

    # Scalar single-cell responses are presented as KPI-style values.
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], tuple) and len(parsed[0]) == 1:
        return f"### Result\n\n## {_format_scalar(parsed[0][0])}"

    if isinstance(parsed, list) and parsed and all(isinstance(item, tuple) for item in parsed):
        tuple_rows = [list(item) for item in parsed]
        col_count = max(len(row) for row in tuple_rows)
        headers = [f"col_{idx}" for idx in range(1, col_count + 1)]
        limited = tuple_rows[:20]
        table = _to_markdown_table(headers=headers, rows=limited)
        suffix = "\n\n_Showing first 20 rows._" if len(tuple_rows) > 20 else ""
        return f"### Result\n\n{table}{suffix}"

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        keys = sorted({key for item in parsed for key in item.keys()})
        rows = [[item.get(key) for key in keys] for item in parsed[:20]]
        table = _to_markdown_table(headers=keys, rows=rows)
        suffix = "\n\n_Showing first 20 rows._" if len(parsed) > 20 else ""
        return f"### Result\n\n{table}{suffix}"

    return f"### Result\n\n```text\n{text}\n```"


def _parse_exec_output(raw_output: object) -> tuple[str, object]:
    text = str(raw_output).strip()
    if not text:
        return "", ""
    try:
        return text, ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text, text


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


def _result_for_rephrase(exec_payload: dict, max_rows: int = 20) -> dict:
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


def _fallback_chat_response(question: str, exec_payload: dict) -> str:
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


def _rephrase_chat_response(question: str, exec_payload: dict) -> str:
    if not exec_payload.get("ok"):
        return _fallback_chat_response(question, exec_payload)

    payload = _result_for_rephrase(exec_payload=exec_payload)
    if payload["status"] == "empty":
        return _fallback_chat_response(question, exec_payload)

    llm_prompt = (
        "You are a data assistant. Rewrite SQL results as a concise direct answer.\n"
        "Rules:\n"
        "1) Use only the provided result payload. Never invent fields, values, or causes.\n"
        "2) Keep output to 1-2 short sentences in plain English.\n"
        "3) Do not include markdown, SQL, or bullet points.\n"
        "4) If the payload is scalar, provide the numeric answer clearly. With at most 2 decimal places.\n"
        "5) If the payload has rows, summarize key values in sentence form.\n"
        "6) If unsure, state limitation briefly and stay factual.\n\n"
        "7) Represent tables in a concise manner. Use markdown tables to represent the data.\n"
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
        response = llm.invoke(llm_prompt)
        content = str(getattr(response, "content", response)).strip()
        if not content:
            return _fallback_chat_response(question, exec_payload)
        return " ".join(content.split())
    except Exception:
        return _fallback_chat_response(question, exec_payload)


def _chat_result_preview(exec_payload: dict) -> str:
    if not exec_payload.get("ok"):
        return f"**Error**\n\n```text\n{exec_payload.get('error')}\n```"

    text, parsed = _parse_exec_output(exec_payload.get("raw_output"))
    if not text:
        return "_No rows returned._"

    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], tuple) and len(parsed[0]) == 1:
        return f"### {_format_scalar(parsed[0][0])}"

    if isinstance(parsed, list) and parsed and all(isinstance(item, tuple) for item in parsed):
        tuple_rows = [list(item) for item in parsed]
        col_count = max(len(row) for row in tuple_rows)
        headers = [f"col_{idx}" for idx in range(1, col_count + 1)]
        table = _to_markdown_table(headers=headers, rows=tuple_rows[:5])
        suffix = "\n\n_Showing first 5 rows in chat._" if len(tuple_rows) > 5 else ""
        return f"{table}{suffix}"

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        keys = sorted({key for item in parsed for key in item.keys()})
        rows = [[item.get(key) for key in keys] for item in parsed[:5]]
        table = _to_markdown_table(headers=keys, rows=rows)
        suffix = "\n\n_Showing first 5 rows in chat._" if len(parsed) > 5 else ""
        return f"{table}{suffix}"

    compact = text[:600] + ("..." if len(text) > 600 else "")
    return f"```text\n{compact}\n```"


def _sql_block(sql_list: list[str]) -> str:
    if not sql_list:
        return ""
    if len(sql_list) == 1:
        return sql_list[0]
    blocks: list[str] = []
    for i, item in enumerate(sql_list, start=1):
        blocks.append(f"-- Statement {i}\n{item}")
    return "\n\n".join(blocks)


def _chat_reply(result: dict) -> str:
    question = str(result.get("question", "")).strip()
    exec_payload = result.get("sql_execution", {})
    return _rephrase_chat_response(question=question, exec_payload=exec_payload)


def chat_handler(
    message: str,
    history: list[dict],
):
    _ = history
    if not message.strip():
        return "Please enter a question.", "", "_No result yet._"

    result = run_single_question(
        question=message,
        db_path=DEFAULT_DB_PATH,
        model=DEFAULT_MODEL,
        temperature=DEFAULT_TEMPERATURE,
        top_k=DEFAULT_TOP_K,
        prefer_verified_templates=True,
    )

    sql_list = result.get("generated_sql_list", [result.get("generated_sql", "")])
    sql_text = _sql_block(sql_list)

    if SAVE_TRACE:
        save_single_trace(DEFAULT_OUTPUT_DIR, DEFAULT_DB_PATH, result)

    formatted_exec = _render_formatted_result(result.get("sql_execution", {}))
    reply = _chat_reply(result)
    return reply, sql_text, formatted_exec


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Child Nutrition LLM-to-SQL") as demo:
        gr.Markdown(
            "## Child Nutrition LLM-to-SQL Demo\n"
            "Ask natural-language questions over `nutrition_data` (DuckDB)."
        )

        sql_code = gr.Code(label="Generated SQL", language="sql")
        formatted_output = gr.Markdown(value="_Run a query to see formatted results._")

        chatbot = gr.Chatbot(height=520)

        gr.ChatInterface(
            fn=chat_handler,
            chatbot=chatbot,
            title="Nutrition Analytics Assistant",
            description="LLM-generated DuckDB SQL over nutrition_data.",
            additional_outputs=[sql_code, formatted_output],
            show_progress="minimal",
            save_history=SAVE_HISTORY,
        )
        gr.Markdown(value=_ground_truth_markdown(VERIFIED_SQL_PATH))

    return demo


def main() -> None:
    if WARMUP_ON_START:
        warmup_runtime(
            db_path=DEFAULT_DB_PATH,
            model=DEFAULT_MODEL,
            temperature=DEFAULT_TEMPERATURE,
        )
    demo = build_app()
    demo.queue(default_concurrency_limit=QUEUE_CONCURRENCY, max_size=QUEUE_MAX_SIZE)
    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        share=os.getenv("GRADIO_SHARE", "false").lower() == "true",
        auth=None,
    )


if __name__ == "__main__":
    main()
