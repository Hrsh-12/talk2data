"""FAISS-backed semantic lookup for previously answered natural-language queries."""

from .index import SemanticQueryIndex
from .records import IndexedQuery

__all__ = ["IndexedQuery", "SemanticQueryIndex"]
