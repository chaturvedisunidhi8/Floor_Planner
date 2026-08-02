"""Engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.sqlalchemy_url

    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # A single-file SQLite DB is shared across FastAPI's threadpool workers.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs |= {"pool_size": 5, "max_overflow": 10}

    logger.info("Database: %s", "postgresql" if settings.uses_postgres else "sqlite (dev)")
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background work."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency - commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables if they do not exist yet."""
    from app import models  # noqa: F401  (registers the mappers)
    from app.db.base import Base

    settings = get_settings()
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=get_engine())
    logger.info("Schema ready")
