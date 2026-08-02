"""Load the JSON template library into the database and build the FAISS index.

    python scripts/seed_database.py

Idempotent: templates are upserted by id, and the index is rebuilt from the
freshly loaded set. Run it after ``author_templates.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.embeddings.encoder import get_encoder
from app.ai.retrieval.vector_store import FaissVectorStore
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import init_db, session_scope
from app.repositories.template_repository import (
    JsonTemplateRepository,
    SqlTemplateRepository,
)

logger = get_logger("seed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-index", action="store_true", help="Load the database but do not build FAISS"
    )
    parser.add_argument(
        "--skip-db", action="store_true", help="Build the index but do not touch the database"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    templates = JsonTemplateRepository(settings.templates_path).list_all()
    logger.info("Loaded %d templates from %s", len(templates), settings.templates_path)

    if not args.skip_db:
        init_db()
        with session_scope() as session:
            count = SqlTemplateRepository(session).upsert_many(templates)
        logger.info(
            "Upserted %d templates into %s",
            count,
            "PostgreSQL" if settings.uses_postgres else "SQLite",
        )

    if not args.skip_index:
        encoder = get_encoder()
        logger.info("Embedding with %s (dim=%d)", encoder.name, encoder.dimension)
        store = FaissVectorStore(encoder)
        store.build(templates)
        store.persist()
        logger.info("FAISS index written to %s", settings.index_path)

    logger.info("Seed complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
