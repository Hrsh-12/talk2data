from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


def project_root() -> Path:
    """Repository root (parent of ``src``)."""
    return Path(__file__).resolve().parents[3]


def default_nutrition_config_path() -> Path:
    return project_root() / "configs" / "nutrition_text_to_sql.yaml"


def _resolve_path(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p).resolve()


def _deep_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _apply_env_to_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Overlay known environment variables onto loaded YAML dict (mutates copy)."""
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
        path = config_path.resolve()
        root = project_root_override or project_root()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid YAML root in {path}")
        raw = _apply_env_to_raw(raw)

        db_rel = _deep_get(raw, "database", "default_relative_path", default="database/nutrition_data.duckdb")
        db_block = raw.get("database") or {}
        ui_rel = db_block.get("ui_default_relative_path")
        ui_resolved = _resolve_path(root, str(ui_rel)) if ui_rel else _resolve_path(root, str(db_rel))
        schema = raw.get("schema") or {}
        tables = schema.get("tables") or ["nutrition_data"]
        if not isinstance(tables, list):
            raise ValueError("schema.tables must be a list")

        cols = schema.get("sample_rows_columns")
        columns_tuple: tuple[str, ...] | None
        if cols is None:
            columns_tuple = None
        elif isinstance(cols, list):
            columns_tuple = tuple(str(c) for c in cols)
        else:
            raise ValueError("schema.sample_rows_columns must be a list or null")

        prompts = raw.get("prompts") or {}
        tmpl_rel = prompts.get("system_template_relative_path", "configs/prompts/nutrition_system.md")

        llm = raw.get("llm") or {}
        eval_block = raw.get("evaluation") or {}
        paths = raw.get("paths") or {}

        return NutriSqlSettings(
            project_root=root,
            database_default_path=_resolve_path(root, str(db_rel)),
            database_ui_default_path=ui_resolved,
            tables=tuple(str(t) for t in tables),
            sample_rows_limit=int(schema.get("sample_rows_limit", 3)),
            sample_rows_columns=columns_tuple,
            prompt_template_path=_resolve_path(root, str(tmpl_rel)),
            llm_model=str(llm.get("model", "gpt-5-mini")),
            llm_temperature=float(llm.get("temperature", 0.0)),
            repair_enabled=bool(llm.get("repair_enabled", True)),
            eval_abs_tolerance=float(eval_block.get("abs_tolerance", 1e-9)),
            eval_rel_tolerance=float(eval_block.get("rel_tolerance", 1e-9)),
            verified_sql_path=_resolve_path(root, str(paths.get("verified_sql_relative_path", "data/queries /queries_verified.sql"))),
            queries_txt_path=_resolve_path(root, str(paths.get("queries_txt_relative_path", "data/queries /queries.txt"))),
            output_dir=_resolve_path(root, str(paths.get("output_dir_relative_path", "outputs"))),
        )


def get_nutrition_settings(config_path: Path | None = None) -> NutriSqlSettings:
    path = (config_path or default_nutrition_config_path()).resolve()
    return NutriSqlSettings.load(path)


def get_default_nutrition_settings() -> NutriSqlSettings:
    """Backward-compatible alias for default config path."""
    return get_nutrition_settings(None)
