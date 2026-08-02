"""FAISS-backed semantic index over the Template Knowledge Base.

Vectors are L2-normalised, so an inner-product index (``IndexFlatIP``) yields
cosine similarity directly. With 20 templates a flat index is exact and
instantaneous; the interface is unchanged if the library grows to thousands and
the index type is swapped for an IVF/HNSW variant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.ai.embeddings.encoder import Encoder, get_encoder
from app.core.config import get_settings
from app.core.exceptions import KnowledgeBaseError
from app.core.logging import get_logger
from app.schemas.template import FloorPlanTemplate

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticHit:
    template_id: str
    score: float


class FaissVectorStore:
    """Build, persist, load and query the template index."""

    def __init__(self, encoder: Encoder | None = None) -> None:
        settings = get_settings()
        self._encoder = encoder or get_encoder()
        self._index_path: Path = settings.index_path
        self._metadata_path: Path = settings.index_metadata_path
        self._index = None
        self._ids: list[str] = []

    # --- Building ---------------------------------------------------------
    def build(self, templates: list[FloorPlanTemplate]) -> None:
        import faiss

        if not templates:
            raise KnowledgeBaseError("Cannot build an index from zero templates")

        texts = [t.to_embedding_text() for t in templates]
        vectors = self._encoder.encode_documents(texts)

        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)

        self._index = index
        self._ids = [t.id for t in templates]
        logger.info("Built FAISS index: %d vectors, dim=%d", index.ntotal, vectors.shape[1])

    def persist(self) -> None:
        import faiss

        if self._index is None:
            raise KnowledgeBaseError("Nothing to persist - build the index first")

        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._metadata_path.write_text(
            json.dumps(
                {
                    "ids": self._ids,
                    "encoder": self._encoder.name,
                    "dimension": self._encoder.dimension,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Persisted index to %s", self._index_path)

    # --- Loading ----------------------------------------------------------
    def load(self) -> bool:
        """Return True when a compatible on-disk index was loaded."""
        import faiss

        if not (self._index_path.exists() and self._metadata_path.exists()):
            return False

        try:
            meta = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            if meta.get("encoder") != self._encoder.name:
                logger.warning(
                    "Index was built with '%s' but the active encoder is '%s'; rebuilding.",
                    meta.get("encoder"),
                    self._encoder.name,
                )
                return False
            self._index = faiss.read_index(str(self._index_path))
            self._ids = list(meta["ids"])
        except Exception as exc:
            logger.warning("Could not load index (%s); it will be rebuilt.", exc)
            return False

        logger.info("Loaded FAISS index with %d vectors", len(self._ids))
        return True

    def ensure_ready(self, templates: list[FloorPlanTemplate]) -> None:
        """Load from disk, rebuilding when missing or stale."""
        if self.load() and set(self._ids) == {t.id for t in templates}:
            return
        self.build(templates)
        try:
            self.persist()
        except Exception as exc:
            logger.warning("Index built in memory but could not be persisted: %s", exc)

    # --- Querying ---------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        return self._index is not None and bool(self._ids)

    @property
    def size(self) -> int:
        return len(self._ids)

    def search(self, query: str, top_k: int) -> list[SemanticHit]:
        if not self.is_ready:
            raise KnowledgeBaseError("Vector index is not ready")

        vector = self._encoder.encode_query(query).reshape(1, -1).astype("float32")
        k = min(top_k, len(self._ids))
        scores, indices = self._index.search(vector, k)  # type: ignore[union-attr]

        hits: list[SemanticHit] = []
        for score, idx in zip(scores[0], indices[0], strict=True):
            if idx < 0:
                continue
            # Cosine on normalised vectors is in [-1, 1]; map into [0, 1].
            hits.append(SemanticHit(self._ids[int(idx)], float((score + 1.0) / 2.0)))
        return hits

    def score_all(self, query: str) -> dict[str, float]:
        """Semantic score for *every* template, keyed by id."""
        return {hit.template_id: hit.score for hit in self.search(query, len(self._ids))}


_store: FaissVectorStore | None = None


def get_vector_store() -> FaissVectorStore:
    global _store
    if _store is None:
        _store = FaissVectorStore()
    return _store


def reset_vector_store() -> None:
    """Test hook."""
    global _store
    _store = None


__all__ = ["FaissVectorStore", "SemanticHit", "get_vector_store", "reset_vector_store"]
