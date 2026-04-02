#!/usr/bin/env python3
"""
gradio_app.py — Gradio UI entry point.

Wires together config, result_utils, and the nutrition SQL service into a
chat interface with a toggleable side panel.
"""
from __future__ import annotations

import os

import gradio as gr
import pandas as pd

# config.py handles ROOT, sys.path, and load_dotenv — must be first.
from config import (
    APP_CSS,
    DEFAULT_DB_PATH,
    DEFAULT_MODEL,
    DEFAULT_TABLE_ROW_LIMIT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_OUTPUT_DIR,
    QUEUE_CONCURRENCY,
    QUEUE_MAX_SIZE,
    SAMPLE_QUERIES,
    SAMPLE_QUERY_LABELS,
    SAVE_HISTORY,
    SAVE_TRACE,
    TIPS_MD,
    VERIFIED_SQL_PATH,
    WARMUP_ON_START,
)
from result_utils import (
    _df_to_markdown_table,
    build_result_table,
    ground_truth_html,
    rephrase_reply,
)
from src.nutrition_sql.service import run_single_question, save_single_trace, warmup_runtime


# ── Chat handler ────────────────────────────────────────────────────────

def chat_handler(message: str, history: list[dict]):
    _ = history
    if not message.strip():
        return "Please enter a question.", "_No result yet._", pd.DataFrame()

    try:
        result = run_single_question(
            question=message,
            db_path=DEFAULT_DB_PATH,
            model=DEFAULT_MODEL,
            temperature=DEFAULT_TEMPERATURE,
            top_k=DEFAULT_TOP_K,
            prefer_verified_templates=True,
        )
    except Exception as exc:
        err = str(exc) or exc.__class__.__name__
        return f"Error: {err}", f"### Result\n\n`ERROR:` {err}", pd.DataFrame({"error": [err]})

    sql_list = result.get("generated_sql_list", [result.get("generated_sql", "")])

    if SAVE_TRACE:
        save_single_trace(DEFAULT_OUTPUT_DIR, DEFAULT_DB_PATH, result)

    exec_payload = result.get("sql_execution", {})
    reply_text = rephrase_reply(
        question=str(result.get("question", "")),
        exec_payload=exec_payload,
    )
    table_df, summary_md = build_result_table(
        executed_sql=str(sql_list[0] if sql_list else ""),
        exec_payload=exec_payload,
        max_rows=DEFAULT_TABLE_ROW_LIMIT,
    )

    # Append a compact table preview in the chat message for multi-row results.
    is_error = not table_df.empty and table_df.shape[1] == 1 and str(table_df.columns[0]).lower() == "error"
    is_scalar = table_df.shape == (1, 1)
    if not table_df.empty and not is_error and not is_scalar:
        n_total = len(table_df)
        preview_md = _df_to_markdown_table(table_df.head(5))
        suffix = (
            f"\n\n_Showing first 5 of {n_total} rows — see **Results** panel for the full table._"
            if n_total > 5 else ""
        )
        reply = f"{reply_text}\n\n{preview_md}{suffix}"
    else:
        reply = reply_text

    return reply, summary_md, table_df


# ── App builder ─────────────────────────────────────────────────────────

def build_app() -> gr.Blocks:
    _theme = gr.themes.Soft(
        primary_hue=gr.themes.colors.blue,
        secondary_hue=gr.themes.colors.indigo,
        neutral_hue=gr.themes.colors.slate,
    )
    gt_html = ground_truth_html(VERIFIED_SQL_PATH)
    sample_md = "\n".join(f"{i}. {q}" for i, q in enumerate(SAMPLE_QUERIES, 1))

    with gr.Blocks(title="Talk2Data", theme=_theme, css=APP_CSS, fill_height=True) as demo:

        # ── Header ───────────────────────────────────────────────────────
        with gr.Row(elem_id="app-header"):
            gr.Markdown(
                "**Talk2Data** &nbsp;·&nbsp; UP Child Nutrition Analytics"
                " &nbsp;·&nbsp; 3.6 M records · Feb–Apr 2024",
                elem_id="app-title",
            )
            toggle_btn = gr.Button(
                "✕ Close Panel", size="sm", scale=0, min_width=120,
                elem_id="panel-toggle-btn",
            )

        panel_visible = gr.State(True)

        # deferred outputs (rendered inside side panel)
        result_summary = gr.Markdown(value="_Run a query to see results._", render=False)
        result_table = gr.Dataframe(
            value=pd.DataFrame(), label="Query Results",
            datatype="auto", interactive=False, wrap=True,
            show_row_numbers=True, show_search="filter",
            buttons=["fullscreen", "copy"], max_height=340,
            render=False,
        )

        # ── Main layout ──────────────────────────────────────────────────
        with gr.Row(equal_height=True):

            with gr.Column(scale=7, elem_id="chat-col"):
                chatbot = gr.Chatbot(show_label=False, elem_id="main-chatbot")
                gr.ChatInterface(
                    fn=chat_handler,
                    chatbot=chatbot,
                    additional_outputs=[result_summary, result_table],
                    show_progress="minimal",
                    save_history=SAVE_HISTORY,
                    examples=SAMPLE_QUERIES,
                    example_labels=SAMPLE_QUERY_LABELS,
                    fill_height=True,
                )

            with gr.Column(scale=3, visible=True, elem_id="side-col") as side_col:
                with gr.Accordion("Getting Started & Tips", open=True):
                    gr.Markdown(TIPS_MD)
                with gr.Accordion("Results", open=False):
                    result_summary.render()
                    result_table.render()
                with gr.Accordion("Sample Queries", open=False):
                    gr.Markdown(sample_md)
                with gr.Accordion("Ground Truth Reference", open=False):
                    gr.HTML(gt_html)

        # ── Panel toggle ─────────────────────────────────────────────────
        def _toggle_panel(vis: bool):
            new_vis = not vis
            return gr.update(visible=new_vis), gr.update(value="✕ Close Panel" if new_vis else "⊞ Open Panel"), new_vis

        toggle_btn.click(_toggle_panel, inputs=[panel_visible], outputs=[side_col, toggle_btn, panel_visible])

    return demo


# ── Entry point ─────────────────────────────────────────────────────────

def main() -> None:
    if WARMUP_ON_START:
        warmup_runtime(db_path=DEFAULT_DB_PATH, model=DEFAULT_MODEL, temperature=DEFAULT_TEMPERATURE)

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
