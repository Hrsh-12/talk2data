"""Project path setup and dotenv. Runtime settings live in Hydra (`conf/`) for entry apps."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()

# Back-compat shims (prefer Hydra cfg + conf/dataset/*.yaml in new code).
from pipeline.paths import DEFAULT_VERIFIED_SQL_FILE  # noqa: E402

DEFAULT_DB_PATH = Path(os.getenv("DB_PATH", "database/nutrition_data.duckdb"))
DEFAULT_QUERIES_PATH = Path(os.getenv("QUERIES_PATH", "data/nutrition_queries.jsonl"))
VERIFIED_SQL_PATH = Path(os.getenv("VERIFIED_SQL_PATH", str(DEFAULT_VERIFIED_SQL_FILE)))
DEFAULT_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
