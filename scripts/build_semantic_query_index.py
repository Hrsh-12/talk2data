#!/usr/bin/env python3
"""
Build the semantic query index (FAISS + JSONL corpus) from a list of natural-language questions.

For each question, runs the text-to-SQL engine, captures execution results, optional intent label,
and optional natural-language summary (rephrase), then writes embedding_index.faiss and
indexed_queries.jsonl under the output directory.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "apps") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps"))

from result_utils import rephrase_reply  # noqa: E402

from src.text_sql.config import get_nutrition_settings  # noqa: E402
from src.text_sql.semantic_cache import IndexedQuery, SemanticQueryIndex  # noqa: E402
from src.text_sql.semantic_cache.embedding_model import SentenceEmbeddingModel  # noqa: E402
from src.text_sql.semantic_cache.intent_classification import classify_query_intent  # noqa: E402
from src.text_sql.service import read_queries_file, run_single_question  # noqa: E402


def _resolve_repo_path(p: Path) -> Path:
    return p if p.is_absolute() else (ROOT / p).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build semantic query FAISS index + JSONL corpus")
    parser.add_argument(
        "--queries-file",
        required=True,
        help="Text file with one natural-language query per line",
    )
    parser.add_argument("--database", default=None, help="Path to DuckDB (default from config / env)")
    parser.add_argument(
        "--output-directory",
        default=None,
        help="Directory for embedding_index.faiss and indexed_queries.jsonl (default: database/semantic_query_cache)",
    )
    parser.add_argument(
        "--embedding-model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="SentenceTransformer model id",
    )
    parser.add_argument(
        "--intent-prompt-path",
        type=Path,
        default=None,
        help="Markdown prompt for intent classification (default: configs/prompts/query_intent_classification.md)",
    )
    parser.add_argument(
        "--skip-intent-classification",
        action="store_true",
        help="Store empty query_intent_summary for each row",
    )
    parser.add_argument(
        "--intent-model",
        default=None,
        help="Chat model for intent classification (default: REPHRASE_MODEL_NAME env or gpt-4o-mini)",
    )
    parser.add_argument("--intent-temperature", type=float, default=0.0)
    parser.add_argument("--config", type=Path, default=None, help="nutrition_text_to_sql.yaml path")
    parser.add_argument("--model", default=None, help="SQL generation model")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit non-zero on first failed SQL execution instead of skipping",
    )
    args = parser.parse_args()

    cfg_path = args.config.resolve() if args.config else None
    settings = get_nutrition_settings(cfg_path)
    db_path = _resolve_repo_path(Path(args.database)) if args.database else settings.database_default_path
    out_dir = (
        _resolve_repo_path(Path(args.output_directory))
        if args.output_directory
        else (ROOT / "database" / "semantic_query_cache").resolve()
    )
    intent_prompt = (
        args.intent_prompt_path.resolve()
        if args.intent_prompt_path
        else (ROOT / "configs" / "prompts" / "query_intent_classification.md").resolve()
    )
    if not intent_prompt.is_file():
        print(f"Intent prompt not found: {intent_prompt}", file=sys.stderr)
        return 1

    queries_path = _resolve_repo_path(Path(args.queries_file))
    queries = read_queries_file(queries_path)
    if not queries:
        print("No queries in file.", file=sys.stderr)
        return 1

    model = args.model if args.model is not None else settings.llm_model
    temperature = settings.llm_temperature if args.temperature is None else float(args.temperature)
    config_kw = {"config_path": cfg_path} if cfg_path else {}

    import os

    intent_model = (
        args.intent_model
        if args.intent_model is not None
        else os.getenv("REPHRASE_MODEL_NAME", "gpt-4o-mini")
    )

    db_stat = db_path.stat()
    mtime_ns = int(db_stat.st_mtime_ns)
    db_path_str = str(db_path.resolve())

    embedder = SentenceEmbeddingModel(args.embedding_model)
    indexed_rows: list[IndexedQuery] = []
    vectors: list[np.ndarray] = []

    for i, query in enumerate(queries):
        print(f"[{i + 1}/{len(queries)}] {query[:80]}{'...' if len(query) > 80 else ''}")
        run_result = run_single_question(
            question=query,
            db_path=db_path,
            model=model,
            temperature=temperature,
            top_k=args.top_k,
            **config_kw,
        )
        sql_exec = run_result.get("sql_execution", {})
        if not sql_exec.get("ok"):
            msg = f"SQL execution failed for query: {sql_exec.get('error')}"
            if args.fail_fast:
                print(msg, file=sys.stderr)
                return 1
            print(f"SKIP: {msg}", file=sys.stderr)
            continue

        sql_list = run_result.get("generated_sql_list", [run_result.get("generated_sql", "")])
        final_sql = str(sql_list[0] if sql_list else "")

        if args.skip_intent_classification:
            intent_summary = ""
        else:
            intent_summary = classify_query_intent(
                natural_language_query=query,
                generated_sql=final_sql,
                model=intent_model,
                temperature=float(args.intent_temperature),
                prompt_path=intent_prompt,
            )

        nl_summary = rephrase_reply(question=query, exec_payload=sql_exec).strip() or None

        vec = embedder.encode_query(query)
        vectors.append(vec[0].copy())

        indexed_rows.append(
            IndexedQuery(
                indexed_query_id=len(indexed_rows),
                natural_language_query=query,
                query_intent_summary=intent_summary,
                generated_sql=final_sql,
                sql_execution=dict(sql_exec),
                natural_language_summary=nl_summary,
                database_path=db_path_str,
                database_file_mtime_ns=mtime_ns,
            )
        )

    if not indexed_rows:
        print("No successful rows to index.", file=sys.stderr)
        return 1

    matrix = np.vstack(vectors).astype(np.float32)
    SemanticQueryIndex.write_artifacts(
        index_directory=out_dir,
        indexed_queries=indexed_rows,
        embedding_matrix=matrix,
    )
    print(f"Wrote {len(indexed_rows)} entries to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
