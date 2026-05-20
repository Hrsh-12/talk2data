"""Tests for semantic query index (no sentence-transformers download)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.text_sql.semantic_cache import IndexedQuery, SemanticQueryIndex


class _FixedAxesEmbeddingModel:
    """Maps phrases to two fixed orthonormal directions for deterministic FAISS scores."""

    def encode_query(self, text: str) -> np.ndarray:
        t = text.lower()
        if "match_axis_a" in t:
            return np.array([[1.0, 0.0]], dtype=np.float32)
        if "match_axis_b" in t:
            return np.array([[0.0, 1.0]], dtype=np.float32)
        return np.array([[1.0, 0.0]], dtype=np.float32)


def _sample_row(idx: int, question: str) -> IndexedQuery:
    return IndexedQuery(
        indexed_query_id=idx,
        natural_language_query=question,
        query_intent_summary="test intent",
        generated_sql="SELECT 1",
        sql_execution={"ok": True, "raw_output": "[(1,)]", "error": None},
        natural_language_summary="One.",
        database_path="/tmp/example.duckdb",
        database_file_mtime_ns=0,
    )


class IndexedQuerySerializationTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        original = _sample_row(0, "How many rows?")
        restored = IndexedQuery.from_json_dict(original.to_json_dict())
        self.assertEqual(restored, original)


class _ObliqueEmbeddingModel:
    """Returns a direction with cosine 0.707 to axis A (below 0.95 threshold)."""

    def encode_query(self, text: str) -> np.ndarray:
        if "weak_match" in text.lower():
            s = 2**0.5 / 2
            return np.array([[s, s]], dtype=np.float32)
        return np.array([[1.0, 0.0]], dtype=np.float32)


class SemanticQueryIndexTests(unittest.TestCase):
    def test_find_best_match_respects_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rows = [
                _sample_row(0, "match_axis_a question one"),
                _sample_row(1, "match_axis_b question two"),
            ]
            matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
            SemanticQueryIndex.write_artifacts(
                index_directory=base,
                indexed_queries=rows,
                embedding_matrix=matrix,
            )
            self.assertTrue(SemanticQueryIndex.artifacts_present(base))

            axis = _FixedAxesEmbeddingModel()
            high = SemanticQueryIndex(
                index_directory=base,
                embedding_model_name="unused",
                minimum_similarity=0.95,
                embedding_model=axis,
            )
            hit, score = high.find_best_match("match_axis_a prevalence")
            self.assertIsNotNone(hit)
            self.assertAlmostEqual(score, 1.0, places=5)
            self.assertEqual(hit.indexed_query_id, 0)

            oblique = _ObliqueEmbeddingModel()
            low = SemanticQueryIndex(
                index_directory=base,
                embedding_model_name="unused",
                minimum_similarity=0.95,
                embedding_model=oblique,
            )
            miss, score2 = low.find_best_match("weak_match unrelated text")
            self.assertIsNone(miss)
            self.assertLess(score2, 0.95)


if __name__ == "__main__":
    unittest.main()
