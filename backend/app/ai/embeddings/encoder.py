"""Text embedding.

Primary encoder is BAAI/bge-small-en-v1.5 via sentence-transformers. A
deterministic hashing encoder stands in when the model is unavailable (offline
CI, first boot before the weights are downloaded) so nothing downstream has to
special-case a missing embedder.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: bge-small-en-v1.5 asks for this prefix on the *query* side only.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Encoder(Protocol):
    dimension: int
    name: str

    def encode_documents(self, texts: list[str]) -> np.ndarray: ...

    def encode_query(self, text: str) -> np.ndarray: ...


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype("float32")


class BGEEncoder:
    """sentence-transformers wrapper around BAAI/bge-small-en-v1.5."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s on %s", model_name, device)
        self._model = SentenceTransformer(model_name, device=device)
        self.dimension = int(self._model.get_sentence_embedding_dimension())
        self.name = model_name

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
        )
        return vectors.astype("float32")

    def encode_query(self, text: str) -> np.ndarray:
        vector = self._model.encode(
            [QUERY_INSTRUCTION + text],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vector.astype("float32")[0]


class HashingEncoder:
    """Offline fallback: deterministic bag-of-token-hashes.

    Not semantically meaningful, but stable, dependency-free and good enough to
    keep the retrieval plumbing exercised. The rule-based scorer dominates the
    final ranking anyway, so results stay sensible without the real model.
    """

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.name = "hashing-fallback"

    def _vectorise(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype="float32")
        tokens = [t for t in text.lower().replace(",", " ").replace(".", " ").split() if t]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return vector

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        return _l2_normalise(np.vstack([self._vectorise(t) for t in texts]))

    def encode_query(self, text: str) -> np.ndarray:
        return _l2_normalise(self._vectorise(text).reshape(1, -1))[0]


_encoder: Encoder | None = None


def get_encoder() -> Encoder:
    """Process-wide singleton - the model costs ~1s and ~120MB to load."""
    global _encoder
    if _encoder is not None:
        return _encoder

    settings = get_settings()
    if settings.embeddings_enabled:
        try:
            _encoder = BGEEncoder(settings.embedding_model, settings.embedding_device)
            return _encoder
        except Exception as exc:
            logger.warning(
                "Could not load '%s' (%s). Falling back to the hashing encoder.",
                settings.embedding_model,
                exc,
            )

    _encoder = HashingEncoder()
    return _encoder


def reset_encoder() -> None:
    """Test hook."""
    global _encoder
    _encoder = None
