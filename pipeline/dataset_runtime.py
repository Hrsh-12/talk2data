"""Resolved dataset settings (from Hydra) for NL→SQL runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, OmegaConf

from pipeline.paths import PROJECT_ROOT, resolve_config_path


def _read_optional_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def duckdb_sqlalchemy_uri(db_path: Path) -> str:
    """URI for LangChain SQLDatabase / SQLAlchemy (absolute DuckDB file path)."""
    resolved = db_path.resolve()
    return f"duckdb:///{resolved}"


@dataclass(frozen=True)
class DatasetRuntime:
    """Per-process dataset configuration (single DB per run)."""

    sqlalchemy_uri: str
    db_path: Path
    queries_path: Path
    output_dir: Path
    schema_tables: tuple[str, ...]
    sample_sql: str | None
    sql_generation_prompt_path: Path
    sql_repair_prompt_path: Path | None
    schema_notes_path: Path | None
    hints_path: Path | None
    db_id: str
    inject_evidence: bool

    @classmethod
    def from_hydra(cls, cfg: DictConfig, original_cwd: Path) -> DatasetRuntime:
        p = cfg.paths
        db_path = resolve_config_path(p.db_path, original_cwd).expanduser()
        queries_path = resolve_config_path(p.queries_path, original_cwd).expanduser()
        output_dir = resolve_config_path(p.output_dir, original_cwd).expanduser()

        ds = cfg.dataset
        st_list = OmegaConf.to_container(ds.schema_tables, resolve=True)
        if not isinstance(st_list, list):
            raise TypeError("dataset.schema_tables must be a list of table names")
        schema_tables = tuple(str(x) for x in st_list)

        ss = OmegaConf.select(ds, "sample_sql", default=None)
        if ss is None or ss == "":
            sample_sql = None
        else:
            sample_sql = str(ss)

        gen_prompt = resolve_config_path(ds.sql_generation_prompt_path, original_cwd).expanduser()
        if not gen_prompt.exists():
            alt = (PROJECT_ROOT / ds.sql_generation_prompt_path).expanduser()
            if alt.exists():
                gen_prompt = alt.resolve()

        repair_raw = OmegaConf.select(ds, "sql_repair_prompt_path", default=None)
        repair_path: Path | None
        if repair_raw:
            repair_path = resolve_config_path(repair_raw, original_cwd).expanduser()
            if not repair_path.exists():
                alt_r = (PROJECT_ROOT / repair_raw).expanduser()
                if alt_r.exists():
                    repair_path = alt_r.resolve()
        else:
            repair_path = None

        notes_raw = ds.get("schema_notes_path")
        schema_notes_path = (
            resolve_config_path(notes_raw, original_cwd).expanduser() if notes_raw else None
        )

        hints_raw = ds.get("hints_path")
        hints_path = resolve_config_path(hints_raw, original_cwd).expanduser() if hints_raw else None

        db_id = str(ds.get("db_id") or "default")
        inject_evidence = bool(ds.get("inject_evidence", False))

        return cls(
            sqlalchemy_uri=duckdb_sqlalchemy_uri(db_path),
            db_path=db_path,
            queries_path=queries_path,
            output_dir=output_dir,
            schema_tables=schema_tables,
            sample_sql=str(sample_sql) if sample_sql else None,
            sql_generation_prompt_path=gen_prompt,
            sql_repair_prompt_path=repair_path,
            schema_notes_path=schema_notes_path,
            hints_path=hints_path,
            db_id=db_id,
            inject_evidence=inject_evidence,
        )

    def hints_block(self) -> str:
        return _read_optional_text(self.hints_path)

    def schema_notes_block(self) -> str:
        return _read_optional_text(self.schema_notes_path)
