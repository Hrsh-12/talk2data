from __future__ import annotations

import numpy as np


def normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize each row to float32 (contiguous) for inner-product cosine search."""
    x = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    out = (x / norms).astype(np.float32, copy=False)
    return np.ascontiguousarray(out)


class SentenceEmbeddingModel:
    """Thin wrapper around sentence-transformers for normalized embeddings."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        self._ensure_loaded()
        emb = self._model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return normalize_rows(np.asarray(emb, dtype=np.float32))

    def encode_query(self, text: str) -> np.ndarray:
        return self.encode_queries([text])
