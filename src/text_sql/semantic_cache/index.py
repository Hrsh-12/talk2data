from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import faiss
import numpy as np

from .embedding_model import SentenceEmbeddingModel
from .records import IndexedQuery

EMBEDDING_INDEX_FILENAME = "embedding_index.faiss"
INDEXED_QUERIES_FILENAME = "indexed_queries.jsonl"


class QueryEmbeddingModel(Protocol):
    def encode_query(self, text: str) -> np.ndarray:
        """Return shape ``(1, dim)`` float32 L2-normalized query embedding."""
        ...


class SemanticQueryIndex:
    """
    Inner-product (cosine) search over normalized query embeddings with a JSONL corpus.
    """

    def __init__(
        self,
        *,
        index_directory: Path,
        embedding_model_name: str,
        minimum_similarity: float,
        embedding_model: QueryEmbeddingModel | None = None,
    ) -> None:
        self._directory = Path(index_directory).resolve()
        self._minimum_similarity = float(minimum_similarity)
        self._embedding_model = embedding_model or SentenceEmbeddingModel(embedding_model_name)
        self._corpus: list[IndexedQuery] = []
        self._faiss_index: faiss.Index | None = None
        self._reload_from_disk()

    @property
    def directory(self) -> Path:
        return self._directory

    @classmethod
    def artifacts_present(cls, index_directory: Path) -> bool:
        base = Path(index_directory)
        return (base / EMBEDDING_INDEX_FILENAME).is_file() and (base / INDEXED_QUERIES_FILENAME).is_file()

    def _reload_from_disk(self) -> None:
        corpus_path = self._directory / INDEXED_QUERIES_FILENAME
        index_path = self._directory / EMBEDDING_INDEX_FILENAME
        if not corpus_path.is_file() or not index_path.is_file():
            raise FileNotFoundError(
                f"Semantic index artifacts missing under {self._directory}: "
                f"expected {INDEXED_QUERIES_FILENAME} and {EMBEDDING_INDEX_FILENAME}"
            )
        rows: list[IndexedQuery] = []
        with corpus_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                row = IndexedQuery.from_json_dict(record)
                if row.indexed_query_id != len(rows):
                    raise ValueError(
                        f"Corpus row order/id mismatch at line {line_no}: "
                        f"expected indexed_query_id {len(rows)}, got {row.indexed_query_id}"
                    )
                rows.append(row)
        index = faiss.read_index(str(index_path))
        if index.ntotal != len(rows):
            raise ValueError(
                f"FAISS index size {index.ntotal} does not match corpus length {len(rows)}"
            )
        self._corpus = rows
        self._faiss_index = index

    def find_best_match(self, natural_language_query: str) -> tuple[IndexedQuery | None, float]:
        if self._faiss_index is None or not self._corpus:
            return None, 0.0
        query_vec = self._embedding_model.encode_query(natural_language_query.strip())
        scores, indices = self._faiss_index.search(query_vec, 1)
        best_score = float(scores[0][0])
        best_idx = int(indices[0][0])
        if best_idx < 0 or best_score < self._minimum_similarity:
            return None, best_score
        return self._corpus[best_idx], best_score

    @staticmethod
    def write_artifacts(
        *,
        index_directory: Path,
        indexed_queries: list[IndexedQuery],
        embedding_matrix: np.ndarray,
    ) -> None:
        """Persist FAISS index and JSONL corpus (embedding rows aligned with corpus order)."""
        index_directory = Path(index_directory).resolve()
        index_directory.mkdir(parents=True, exist_ok=True)
        vectors = np.asarray(embedding_matrix, dtype=np.float32)
        if vectors.shape[0] != len(indexed_queries):
            raise ValueError("embedding_matrix row count must match indexed_queries length")
        dim = int(vectors.shape[1])
        index = faiss.IndexFlatIP(dim)
        index.add(np.ascontiguousarray(vectors))
        faiss.write_index(index, str(index_directory / EMBEDDING_INDEX_FILENAME))

        corpus_path = index_directory / INDEXED_QUERIES_FILENAME
        tmp = corpus_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in indexed_queries:
                handle.write(json.dumps(row.to_json_dict(), ensure_ascii=False) + "\n")
        tmp.replace(corpus_path)
