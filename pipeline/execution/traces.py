"""Trace I/O: batch JSON, query list files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


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
        "llm_input": result["llm_input"],
        "llm_raw_output": result["llm_raw_output"],
        "generated_sql": result["generated_sql"],
        "generated_sql_list": result.get("generated_sql_list", [result["generated_sql"]]),
        "template_route": result.get("template_route"),
        "repair_llm_output": result["repair_llm_output"],
        "repaired_sql": result["repaired_sql"],
        "sql_execution": result["sql_execution"],
        "comparison": result["comparison"],
    }
    return save_batch_outputs(
        output_dir=output_dir,
        queries_file=Path("__single_question__"),
        db_path=db_path,
        results=[item],
    )
