from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ── Project root & path setup ───────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.text_sql.utils import get_nutrition_settings

load_dotenv()

# ── Nutrition engine defaults (YAML + env) ─────────────────────────────
_sql_settings = get_nutrition_settings(None)

_db_env = os.getenv("DB_PATH")
if _db_env:
    _db_p = Path(_db_env)
    DEFAULT_DB_PATH = _db_p if _db_p.is_absolute() else (ROOT / _db_p).resolve()
else:
    DEFAULT_DB_PATH = _sql_settings.database_ui_default_path

VERIFIED_SQL_PATH = _sql_settings.verified_sql_path
_out_env = os.getenv("OUTPUT_DIR")
if _out_env:
    _op = Path(_out_env)
    DEFAULT_OUTPUT_DIR = _op.resolve() if _op.is_absolute() else (ROOT / _op).resolve()
else:
    DEFAULT_OUTPUT_DIR = _sql_settings.output_dir

# ── SQL / model settings ───────────────────────────────────────────────
DEFAULT_MODEL = os.getenv("MODEL_NAME", _sql_settings.llm_model)
DEFAULT_TOP_K = int(os.getenv("TOP_K", "5"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", str(_sql_settings.llm_temperature)))
DEFAULT_TABLE_ROW_LIMIT = 10

REPHRASE_MODEL = os.getenv("REPHRASE_MODEL_NAME", "gpt-4o-mini")
REPHRASE_TEMPERATURE = float(os.getenv("REPHRASE_TEMPERATURE", "0.0"))
REPHRASE_MAX_TOKENS = int(os.getenv("REPHRASE_MAX_TOKENS", "96"))
REPHRASE_TIMEOUT_SECONDS = float(os.getenv("REPHRASE_TIMEOUT_SECONDS", "20"))
REPHRASE_MAX_RETRIES = int(os.getenv("REPHRASE_MAX_RETRIES", "1"))

# ── Runtime flags ───────────────────────────────────────────────────────
SAVE_TRACE = os.getenv("SAVE_TRACE", "true").lower() == "true"
SAVE_HISTORY = os.getenv("GRADIO_SAVE_HISTORY", "true").lower() == "true"
QUEUE_CONCURRENCY = int(os.getenv("GRADIO_QUEUE_CONCURRENCY", "2"))
QUEUE_MAX_SIZE = int(os.getenv("GRADIO_QUEUE_MAX_SIZE", "32"))
WARMUP_ON_START = os.getenv("WARMUP_ON_START", "true").lower() == "true"

# ── Semantic query cache (FAISS + JSONL corpus) ─────────────────────────
_sem_cache_enabled = os.getenv("SEMANTIC_QUERY_CACHE_ENABLED", "true").lower() == "true"
_sem_cache_dir_env = os.getenv("SEMANTIC_QUERY_CACHE_DIRECTORY")
if _sem_cache_dir_env:
    _scd = Path(_sem_cache_dir_env)
    SEMANTIC_QUERY_CACHE_DIRECTORY = _scd if _scd.is_absolute() else (ROOT / _scd).resolve()
else:
    SEMANTIC_QUERY_CACHE_DIRECTORY = (ROOT / "database" / "semantic_query_cache").resolve()

SEMANTIC_QUERY_CACHE_ENABLED = _sem_cache_enabled
SEMANTIC_QUERY_CACHE_MIN_SIMILARITY = float(os.getenv("SEMANTIC_QUERY_CACHE_MIN_SIMILARITY", "0.95"))
SEMANTIC_QUERY_CACHE_EMBEDDING_MODEL = os.getenv(
    "SEMANTIC_QUERY_CACHE_EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
SEMANTIC_QUERY_CACHE_REEXECUTE_ON_HIT = (
    os.getenv("SEMANTIC_QUERY_CACHE_REEXECUTE_ON_HIT", "false").lower() == "true"
)


def _load_gradio_ui_bundle() -> tuple[list[str], list[str], str, str]:
    ui_path = ROOT / "configs" / "gradio_ui.yaml"
    data = yaml.safe_load(ui_path.read_text(encoding="utf-8")) or {}
    rel = data.get("sample_queries_relative_path", "configs/gradio_sample_queries.yaml")
    sample_path = (ROOT / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    samples_doc = yaml.safe_load(sample_path.read_text(encoding="utf-8")) or {}
    items = samples_doc.get("sample_query_items") or []
    queries: list[str] = []
    labels: list[str] = []
    for it in items:
        q = str(it.get("query", "")).strip()
        if not q:
            continue
        queries.append(q)
        labels.append(str(it.get("label", "")).strip())
    tips = str(data.get("tips_md") or "")
    css = str(data.get("app_css") or "")
    return queries, labels, tips, css


SAMPLE_QUERIES, SAMPLE_QUERY_LABELS, TIPS_MD, APP_CSS = _load_gradio_ui_bundle()
