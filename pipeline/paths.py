"""Canonical filesystem paths and Hydra-relative path resolution."""

from __future__ import annotations

from pathlib import Path

# Project root: pipeline/paths.py -> parents[1] == repo root
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Historical directory name includes a trailing space (see README / docs).
QUERIES_DIR = PROJECT_ROOT / "data" / "queries "
DEFAULT_VERIFIED_SQL_FILE = QUERIES_DIR / "queries_verified.sql"
DEFAULT_QUERIES_TXT = QUERIES_DIR / "queries.txt"


def resolve_config_path(path_str: str | Path, original_cwd: Path) -> Path:
    """Resolve a config path against ``get_original_cwd()`` when Hydra leaves cwd unchanged."""
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p
    return (original_cwd / p).resolve()
