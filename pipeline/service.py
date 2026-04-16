"""
Facade for the NL→SQL query stack.

Import from here for a stable public API, e.g.
``from pipeline.service import run_single_question``.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from pipeline.benchmark.verification import compare_generated_with_verified
from pipeline.db.engine import warmup_runtime
from pipeline.execution.runner import (
    run_query_dataframe,
    run_single_question,
)
from pipeline.execution.traces import read_queries_file, save_batch_outputs, save_single_trace
from pipeline.queries.catalog import (
    load_query_catalog,
    load_verified_sql_by_question_id,
    parse_verified_sql_by_query,
)

__all__ = [
    "compare_generated_with_verified",
    "load_query_catalog",
    "load_verified_sql_by_question_id",
    "parse_verified_sql_by_query",
    "read_queries_file",
    "run_query_dataframe",
    "run_single_question",
    "save_batch_outputs",
    "save_single_trace",
    "warmup_runtime",
]

