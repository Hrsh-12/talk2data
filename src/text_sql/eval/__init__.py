"""Golden-SQL parsing, generated-vs-verified comparison, and eval metrics."""

from .metrics import EvalReport, aggregate_verdicts
from .verification import (
    compare_generated_with_verified,
    compare_structured_values,
    parse_verified_sql_by_query,
)

__all__ = [
    "EvalReport",
    "aggregate_verdicts",
    "compare_generated_with_verified",
    "compare_structured_values",
    "parse_verified_sql_by_query",
]
