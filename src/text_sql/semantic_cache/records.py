from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IndexedQuery:
    """One row of the semantic query corpus, aligned with a FAISS vector id."""

    indexed_query_id: int
    natural_language_query: str
    query_intent_summary: str
    generated_sql: str
    sql_execution: dict[str, Any]
    natural_language_summary: str | None
    database_path: str
    database_file_mtime_ns: int

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> IndexedQuery:
        return cls(
            indexed_query_id=int(data["indexed_query_id"]),
            natural_language_query=str(data["natural_language_query"]),
            query_intent_summary=str(data.get("query_intent_summary") or ""),
            generated_sql=str(data["generated_sql"]),
            sql_execution=dict(data["sql_execution"]),
            natural_language_summary=(
                str(data["natural_language_summary"])
                if data.get("natural_language_summary") not in (None, "")
                else None
            ),
            database_path=str(data["database_path"]),
            database_file_mtime_ns=int(data["database_file_mtime_ns"]),
        )
