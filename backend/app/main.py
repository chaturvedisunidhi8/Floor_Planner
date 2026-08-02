"""Application factory and startup wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.embeddings.encoder import get_encoder
from app.ai.retrieval.vector_store import get_vector_store
from app.api.error_handlers import register_error_handlers
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import init_db
from app.repositories.template_repository import JsonTemplateRepository

logger = get_logger(__name__)

DESCRIPTION = """
AI-powered floor planner.

Users choose their requirements through a guided interface; the agent analyses
them, searches a knowledge base of 20 digitised residential floor plans, and
generates several realistic layout images from the closest matches.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the expensive singletons so the first request is not the slow one."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)

    settings.storage_path.mkdir(parents=True, exist_ok=True)
    settings.index_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        init_db()
    except Exception:
        logger.exception("Database initialisation failed; sessions will not be persisted")

    try:
        encoder = get_encoder()
        templates = JsonTemplateRepository(settings.templates_path).list_all()
        get_vector_store().ensure_ready(templates)
        logger.info(
            "Knowledge base ready: %d templates indexed with %s", len(templates), encoder.name
        )
    except Exception:
        logger.exception("Vector index warm-up failed; scoring will fall back to rules")

    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": settings.app_name,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
