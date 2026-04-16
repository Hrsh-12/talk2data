#!/usr/bin/env python3
"""
gradio_app.py — Gradio UI entry point.

Wires together Hydra config, dataset runtime, result_utils, and the NL→SQL service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_APPS_DIR = Path(__file__).resolve().parent
_ROOT = _APPS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_APPS_DIR) not in sys.path:
    sys.path.insert(0, str(_APPS_DIR))

from dotenv import load_dotenv

load_dotenv()

import gradio as gr
import hydra
import pandas as pd
from hydra.utils import get_original_cwd, instantiate
from omegaconf import DictConfig, OmegaConf

from pipeline.dataset_runtime import DatasetRuntime
from pipeline.paths import resolve_config_path
from pipeline.queries.catalog import load_query_catalog
from pipeline.service import run_single_question, save_single_trace, warmup_runtime

from ui_content import (  # noqa: E402
    APP_CSS,
    SAMPLE_QUERIES,
    SAMPLE_QUERY_LABELS,
    TIPS_MD,
)
from result_utils import (  # noqa: E402
    _df_to_markdown_table,
    build_result_table,
    ground_truth_html,
    rephrase_reply,
)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() == "true"


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    return int(v)


def _rephrase_kwargs(cfg: DictConfig) -> dict:
    r = cfg.rephrase
    return {
        "model": os.getenv("REPHRASE_MODEL_NAME", r.model),
        "temperature": float(os.getenv("REPHRASE_TEMPERATURE", str(r.temperature))),
        "max_tokens": int(os.getenv("REPHRASE_MAX_TOKENS", str(r.max_tokens))),
        "timeout_seconds": float(os.getenv("REPHRASE_TIMEOUT_SECONDS", str(r.timeout_seconds))),
        "max_retries": int(os.getenv("REPHRASE_MAX_RETRIES", str(r.max_retries))),
    }


def _ui_samples(cfg: DictConfig, queries_path: Path) -> tuple[list[str], list[str]]:
    ds_ui = OmegaConf.select(cfg, "dataset.ui", default=None) or {}
    sq = OmegaConf.select(ds_ui, "sample_queries", default=None)
    if sq:
        labels = OmegaConf.select(ds_ui, "sample_query_labels", default=None)
        qs = [str(x) for x in sq]
        if labels:
            lab = [str(x) for x in labels]
        else:
            lab = [f"Example {i + 1}" for i in range(len(qs))]
        return qs, lab
    try:
        cat = load_query_catalog(queries_path)
        qs = [str(r["question"]) for r in cat[:8] if r.get("question")]
        lab = [f"Q{r.get('question_id', i)}" for i, r in enumerate(cat[: len(qs)])]
        if qs:
            return qs, lab
    except Exception:
        pass
    return list(SAMPLE_QUERIES), list(SAMPLE_QUERY_LABELS)


def _tips_markdown(cfg: DictConfig, orig: Path) -> str:
    ds_ui = OmegaConf.select(cfg, "dataset.ui", default=None) or {}
    raw = OmegaConf.select(ds_ui, "tips_markdown", default=None)
    if not raw:
        return TIPS_MD
    p = resolve_config_path(raw, orig).expanduser()
    if p.exists():
        return p.read_text(encoding="utf-8")
    return TIPS_MD


def build_app(
    *,
    runtime: DatasetRuntime,
    queries_path: Path,
    output_dir: Path,
    llm,
    merged_model: str,
    merged_temp: float,
    merged_top_k: int,
    save_trace: bool,
    save_history: bool,
    rephrase_kw: dict,
    table_row_limit: int,
    ui_title: str,
    ui_subtitle: str,
    tips_md: str,
    sample_queries: list[str],
    sample_query_labels: list[str],
    ground_truth_db_id_filter: str | None,
) -> gr.Blocks:
    def chat_handler(message: str, history: list[dict]):
        _ = history
        if not message.strip():
            return "Please enter a question.", "_No result yet._", pd.DataFrame()

        try:
            result = run_single_question(
                question=message,
                runtime=runtime,
                model=merged_model,
                temperature=merged_temp,
                top_k=merged_top_k,
                llm=llm,
            )
        except Exception as exc:
            err = str(exc) or exc.__class__.__name__
            return f"Error: {err}", f"### Result\n\n`ERROR:` {err}", pd.DataFrame({"error": [err]})

        sql_list = result.get("generated_sql_list", [result.get("generated_sql", "")])

        if save_trace:
            save_single_trace(output_dir, runtime.db_path, result)

        exec_payload = result.get("sql_execution", {})
        reply_text = rephrase_reply(
            question=str(result.get("question", "")),
            exec_payload=exec_payload,
            **rephrase_kw,
        )
        table_df, summary_md = build_result_table(
            executed_sql=str(sql_list[0] if sql_list else ""),
            exec_payload=exec_payload,
            max_rows=table_row_limit,
            sqlalchemy_uri=runtime.sqlalchemy_uri,
        )

        is_error = not table_df.empty and table_df.shape[1] == 1 and str(table_df.columns[0]).lower() == "error"
        is_scalar = table_df.shape == (1, 1)
        if not table_df.empty and not is_error and not is_scalar:
            n_total = len(table_df)
            preview_md = _df_to_markdown_table(table_df.head(5))
            suffix = (
                f"\n\n_Showing first 5 of {n_total} rows — see **Results** panel for the full table._"
                if n_total > 5
                else ""
            )
            reply = f"{reply_text}\n\n{preview_md}{suffix}"
        else:
            reply = reply_text

        return reply, summary_md, table_df

    _theme = gr.themes.Soft(
        primary_hue=gr.themes.colors.blue,
        secondary_hue=gr.themes.colors.indigo,
        neutral_hue=gr.themes.colors.slate,
    )
    gt_html = ground_truth_html(queries_path, db_id_filter=ground_truth_db_id_filter)
    sample_md = "\n".join(f"{i}. {q}" for i, q in enumerate(sample_queries, 1))

    with gr.Blocks(title=ui_title, theme=_theme, css=APP_CSS, fill_height=True) as demo:

        with gr.Row(elem_id="app-header"):
            gr.Markdown(
                f"**{ui_title}** &nbsp;·&nbsp; {ui_subtitle}",
                elem_id="app-title",
            )
            toggle_btn = gr.Button(
                "✕ Close Panel",
                size="sm",
                scale=0,
                min_width=120,
                elem_id="panel-toggle-btn",
            )

        panel_visible = gr.State(True)

        result_summary = gr.Markdown(value="_Run a query to see results._", render=False)
        result_table = gr.Dataframe(
            value=pd.DataFrame(),
            label="Query Results",
            datatype="auto",
            interactive=False,
            wrap=True,
            show_row_numbers=True,
            show_search="filter",
            buttons=["fullscreen", "copy"],
            max_height=340,
            render=False,
        )

        with gr.Row(equal_height=True):

            with gr.Column(scale=7, elem_id="chat-col"):
                chatbot = gr.Chatbot(show_label=False, elem_id="main-chatbot")
                gr.ChatInterface(
                    fn=chat_handler,
                    chatbot=chatbot,
                    additional_outputs=[result_summary, result_table],
                    show_progress="minimal",
                    save_history=save_history,
                    examples=sample_queries,
                    example_labels=sample_query_labels,
                    fill_height=True,
                )

            with gr.Column(scale=3, visible=True, elem_id="side-col") as side_col:
                with gr.Accordion("Getting Started & Tips", open=True):
                    gr.Markdown(tips_md)
                with gr.Accordion("Results", open=False):
                    result_summary.render()
                    result_table.render()
                with gr.Accordion("Sample Queries", open=False):
                    gr.Markdown(sample_md)
                with gr.Accordion("Ground Truth Reference", open=False):
                    gr.HTML(gt_html)

        def _toggle_panel(vis: bool):
            new_vis = not vis
            return (
                gr.update(visible=new_vis),
                gr.update(value="✕ Close Panel" if new_vis else "⊞ Open Panel"),
                new_vis,
            )

        toggle_btn.click(_toggle_panel, inputs=[panel_visible], outputs=[side_col, toggle_btn, panel_visible])

    return demo


@hydra.main(version_base=None, config_path="../conf", config_name="config")
def main(cfg: DictConfig) -> None:
    orig = Path(get_original_cwd())
    runtime = DatasetRuntime.from_hydra(cfg, orig)
    queries_path = runtime.queries_path

    merged_model = os.getenv("MODEL_NAME", cfg.llm.model)
    merged_temp = float(os.getenv("TEMPERATURE", str(cfg.llm.temperature)))
    merged_top_k = int(os.getenv("TOP_K", str(cfg.llm.top_k)))
    llm = instantiate(
        {
            "_target_": "langchain_openai.ChatOpenAI",
            "model": merged_model,
            "temperature": merged_temp,
        },
        _convert_="all",
    )

    save_trace = _env_bool("SAVE_TRACE", cfg.gradio.save_trace)
    save_history = _env_bool("GRADIO_SAVE_HISTORY", cfg.gradio.save_history)
    warmup_on = _env_bool("WARMUP_ON_START", cfg.gradio.warmup_on_start)
    qc = _env_int("GRADIO_QUEUE_CONCURRENCY", cfg.gradio.queue_concurrency)
    qm = _env_int("GRADIO_QUEUE_MAX_SIZE", cfg.gradio.queue_max_size)

    server_name = os.getenv("GRADIO_SERVER_NAME", cfg.gradio.server_name)
    server_port = _env_int("GRADIO_SERVER_PORT", cfg.gradio.server_port)
    share_env = os.getenv("GRADIO_SHARE")
    share = (share_env.lower() == "true") if share_env is not None else bool(cfg.gradio.share)

    ds_ui = OmegaConf.select(cfg, "dataset.ui", default=None) or {}
    ui_title = str(OmegaConf.select(ds_ui, "title", default="Talk2Data"))
    ui_subtitle = str(OmegaConf.select(ds_ui, "subtitle", default=""))

    sample_queries, sample_query_labels = _ui_samples(cfg, queries_path)
    tips_md = _tips_markdown(cfg, orig)

    if warmup_on:
        warmup_runtime(
            db_path=runtime.db_path,
            sqlalchemy_uri=runtime.sqlalchemy_uri,
            schema_tables=runtime.schema_tables,
            sample_sql=runtime.sample_sql,
            model=merged_model,
            temperature=merged_temp,
        )

    demo = build_app(
        runtime=runtime,
        queries_path=queries_path,
        output_dir=runtime.output_dir,
        llm=llm,
        merged_model=merged_model,
        merged_temp=merged_temp,
        merged_top_k=merged_top_k,
        save_trace=save_trace,
        save_history=save_history,
        rephrase_kw=_rephrase_kwargs(cfg),
        table_row_limit=int(cfg.gradio.table_row_limit),
        ui_title=ui_title,
        ui_subtitle=ui_subtitle,
        tips_md=tips_md,
        sample_queries=sample_queries,
        sample_query_labels=sample_query_labels,
        ground_truth_db_id_filter=None,
    )
    demo.queue(default_concurrency_limit=qc, max_size=qm)
    demo.launch(
        server_name=server_name,
        server_port=server_port,
        share=share,
        auth=None,
    )


if __name__ == "__main__":
    main()
