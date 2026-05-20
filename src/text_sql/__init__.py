"""Natural-language to SQL pipeline (DuckDB + LangChain + OpenAI)."""

from .service import (
    compare_generated_with_verified,
    default_database_path,
    default_nutrition_config_path,
    default_output_dir,
    default_queries_txt_path,
    default_verified_sql_path,
    parse_verified_sql_by_query,
    read_queries_file,
    run_query_dataframe,
    run_single_question,
    save_batch_outputs,
    save_single_trace,
    warmup_runtime,
)

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
