"""Golden-SQL parsing and generated-vs-verified comparison."""

from .verification import (
    compare_generated_with_verified,
    compare_structured_values,
    parse_verified_sql_by_query,
)

__all__ = [
    "compare_generated_with_verified",
    "compare_structured_values",
    "parse_verified_sql_by_query",
]
