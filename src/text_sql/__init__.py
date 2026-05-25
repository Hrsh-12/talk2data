"""Natural-language to SQL pipeline (DuckDB + LangChain + OpenAI)."""

from .engine import TextToSQLEngine
from .eval import compare_generated_with_verified, parse_verified_sql_by_query
from .utils import (
    default_nutrition_config_path,
    get_nutrition_settings,
    read_queries_file,
    save_batch_outputs,
    save_single_trace,
)

__all__ = [
    "TextToSQLEngine",
    "compare_generated_with_verified",
    "default_nutrition_config_path",
    "get_nutrition_settings",
    "parse_verified_sql_by_query",
    "read_queries_file",
    "save_batch_outputs",
    "save_single_trace",
]
