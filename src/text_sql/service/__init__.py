from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..config.settings import default_nutrition_config_path, get_nutrition_settings
from ..engine.pipeline import TextToSQLEngine
from ..eval.verification import compare_generated_with_verified, parse_verified_sql_by_query
from ..sql.execute import run_query_dataframe

load_dotenv()


def warmup_runtime(
    db_path: Path,
    model: str,
    temperature: float,
    *,
    config_path: Path | None = None,
) -> None:
    TextToSQLEngine.from_config(config_path).warmup(db_path, model, temperature)


def run_single_question(
    question: str,
    db_path: Path,
    model: str,
    temperature: float,
    top_k: int,
    prefer_verified_templates: bool | None = None,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    _ = prefer_verified_templates  # Backward-compatible no-op: routing removed.
    engine = TextToSQLEngine.from_config(config_path)
    return engine.run_single_question(
        question=question,
        db_path=db_path,
        model=model,
        temperature=temperature,
        top_k=top_k,
    )


def read_queries_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Queries file not found: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_batch_outputs(
    output_dir: Path,
    queries_file: Path,
    db_path: Path,
    results: list[dict[str, Any]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"llm_to_sql_trace_{timestamp}.json"
    latest_json = output_dir / "llm_to_sql_trace_latest.json"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "queries_file": str(queries_file),
        "db_path": str(db_path),
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path


def save_single_trace(output_dir: Path, db_path: Path, result: dict[str, Any]) -> Path:
    item = {
        "query_index": 1,
        "query": result["question"],
        "llm_input": result.get("llm_input", ""),
        "llm_raw_output": result.get("llm_raw_output", ""),
        "generated_sql": result.get("generated_sql", ""),
        "generated_sql_list": result.get("generated_sql_list", [result.get("generated_sql", "")]),
        "template_route": result.get("template_route"),
        "repair_llm_output": result.get("repair_llm_output"),
        "repaired_sql": result.get("repaired_sql"),
        "sql_execution": result["sql_execution"],
        "comparison": result.get(
            "comparison",
            {
                "checked": False,
                "verdict": "not_checked",
                "reason": "No comparison payload.",
                "exact_sql_match": None,
                "same_result": None,
            },
        ),
    }
    if "semantic_cache_lookup" in result:
        item["semantic_cache_lookup"] = result["semantic_cache_lookup"]
    return save_batch_outputs(
        output_dir=output_dir,
        queries_file=Path("__single_question__"),
        db_path=db_path,
        results=[item],
    )


def default_queries_txt_path() -> Path:
    return get_nutrition_settings(None).queries_txt_path


def default_verified_sql_path() -> Path:
    return get_nutrition_settings(None).verified_sql_path


def default_output_dir() -> Path:
    return get_nutrition_settings(None).output_dir


def default_database_path() -> Path:
    return get_nutrition_settings(None).database_default_path


__all__ = [
    "compare_generated_with_verified",
    "default_database_path",
    "default_nutrition_config_path",
    "default_output_dir",
    "default_queries_txt_path",
    "default_verified_sql_path",
    "parse_verified_sql_by_query",
    "read_queries_file",
    "run_query_dataframe",
    "run_single_question",
    "save_batch_outputs",
    "save_single_trace",
    "warmup_runtime",
]
