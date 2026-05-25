from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class NutriSqlSettings:
    project_root: Path
    database_default_path: Path
    database_ui_default_path: Path
    tables: tuple[str, ...]
    sample_rows_limit: int
    sample_rows_columns: tuple[str, ...] | None
    prompt_template_path: Path
    llm_model: str
    llm_temperature: float
    repair_enabled: bool
    eval_abs_tolerance: float
    eval_rel_tolerance: float
    verified_sql_path: Path
    queries_txt_path: Path
    output_dir: Path

    @staticmethod
    def load(
        config_path: Path,
        *,
        project_root_override: Path | None = None,
    ) -> NutriSqlSettings:
        return load_nutrition_settings(config_path, project_root_override=project_root_override)


def project_root() -> Path:
    """Repository root (parent of ``src``)."""
    return Path(__file__).resolve().parents[3]


def default_nutrition_config_path() -> Path:
    return project_root() / "configs" / "nutrition_text_to_sql.yaml"


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def deep_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def apply_env_to_raw_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Overlay known environment variables onto a loaded YAML config."""
    data = dict(raw)
    if os.getenv("MODEL_NAME"):
        data.setdefault("llm", {})
        if isinstance(data["llm"], dict):
            data["llm"]["model"] = os.environ["MODEL_NAME"]
    if os.getenv("TEMPERATURE") is not None:
        data.setdefault("llm", {})
        if isinstance(data["llm"], dict):
            try:
                data["llm"]["temperature"] = float(os.environ["TEMPERATURE"])
            except ValueError:
                pass
    if os.getenv("OUTPUT_DIR"):
        data.setdefault("paths", {})
        if isinstance(data["paths"], dict):
            data["paths"]["output_dir_relative_path"] = os.environ["OUTPUT_DIR"]
    return data


def load_nutrition_settings(
    config_path: Path,
    *,
    project_root_override: Path | None = None,
) -> NutriSqlSettings:
    path = config_path.resolve()
    root = project_root_override or project_root()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid YAML root in {path}")
    raw = apply_env_to_raw_config(raw)

    db_rel = deep_get(
        raw,
        "database",
        "default_relative_path",
        default="database/nutrition_data.duckdb",
    )
    db_block = raw.get("database") or {}
    ui_rel = db_block.get("ui_default_relative_path")
    ui_resolved = resolve_path(root, str(ui_rel)) if ui_rel else resolve_path(root, str(db_rel))

    schema = raw.get("schema") or {}
    tables = schema.get("tables") or ["nutrition_data"]
    if not isinstance(tables, list):
        raise ValueError("schema.tables must be a list")

    columns = schema.get("sample_rows_columns")
    if columns is None:
        columns_tuple: tuple[str, ...] | None = None
    elif isinstance(columns, list):
        columns_tuple = tuple(str(column) for column in columns)
    else:
        raise ValueError("schema.sample_rows_columns must be a list or null")

    prompts = raw.get("prompts") or {}
    template_rel = prompts.get("system_template_relative_path", "configs/prompts/nutrition_system.md")
    llm = raw.get("llm") or {}
    eval_block = raw.get("evaluation") or {}
    paths = raw.get("paths") or {}

    return NutriSqlSettings(
        project_root=root,
        database_default_path=resolve_path(root, str(db_rel)),
        database_ui_default_path=ui_resolved,
        tables=tuple(str(table) for table in tables),
        sample_rows_limit=int(schema.get("sample_rows_limit", 3)),
        sample_rows_columns=columns_tuple,
        prompt_template_path=resolve_path(root, str(template_rel)),
        llm_model=str(llm.get("model", "gpt-5-mini")),
        llm_temperature=float(llm.get("temperature", 0.0)),
        repair_enabled=bool(llm.get("repair_enabled", True)),
        eval_abs_tolerance=float(eval_block.get("abs_tolerance", 1e-9)),
        eval_rel_tolerance=float(eval_block.get("rel_tolerance", 1e-9)),
        verified_sql_path=resolve_path(
            root,
            str(paths.get("verified_sql_relative_path", "data/queries /queries_verified.sql")),
        ),
        queries_txt_path=resolve_path(
            root,
            str(paths.get("queries_txt_relative_path", "data/queries /queries.txt")),
        ),
        output_dir=resolve_path(root, str(paths.get("output_dir_relative_path", "outputs"))),
    )


def get_nutrition_settings(config_path: Path | None = None) -> NutriSqlSettings:
    path = (config_path or default_nutrition_config_path()).resolve()
    return load_nutrition_settings(path)


def build_prompt(settings: NutriSqlSettings, table_info: str, sample_rows_text: str, top_k: int) -> str:
    return (
        settings.prompt_template_path.read_text(encoding="utf-8")
        .replace("__TABLE_INFO__", table_info)
        .replace("__SAMPLE_ROWS__", sample_rows_text)
        .replace("__TOP_K__", str(top_k))
    )


def build_sample_sql(table: str, limit: int, columns: tuple[str, ...] | None) -> str:
    if not columns:
        return f"SELECT * FROM {table} LIMIT {limit}"

    def quote_col(column: str) -> str:
        return '"' + column.replace('"', '""') + '"'

    quoted = ", ".join(quote_col(column) for column in columns)
    return f"SELECT {quoted} FROM {table} LIMIT {limit}"


def get_table_info_and_samples(settings: NutriSqlSettings, db_path: Path) -> tuple[str, str]:
    from langchain_community.utilities import SQLDatabase

    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_list = list(settings.tables)
    table_info = db.get_table_info(table_list)
    sample_sql = build_sample_sql(
        table_list[0],
        settings.sample_rows_limit,
        settings.sample_rows_columns,
    )
    sample_rows_text = db.run(sample_sql)
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return table_info, sample_rows_text


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