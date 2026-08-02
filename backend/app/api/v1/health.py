"""Liveness and capability reporting."""

from __future__ import annotations

from fastapi import APIRouter

from app.ai.embeddings.encoder import get_encoder
from app.ai.imaging.pipeline import get_image_pipeline
from app.ai.llm.client import get_llm_client
from app.api.deps import SettingsDep, TemplateRepositoryDep

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    return {"status": "ok"}


@router.get("/status", summary="Which subsystems are live")
def status(settings: SettingsDep, repository: TemplateRepositoryDep) -> dict:
    """Reports what is actually wired up, so a missing key is obvious at a glance."""
    pipeline = get_image_pipeline()
    llm = get_llm_client()

    try:
        template_count = repository.count()
    except Exception:
        template_count = 0

    return {
        "status": "ok",
        "environment": settings.app_env,
        "knowledge_base": {
            "templates": template_count,
            "source": type(repository).__name__,
        },
        "llm": {
            "enabled": llm.enabled,
            "model": settings.groq_model,
        },
        "embeddings": {
            "model": get_encoder().name,
            "dimension": get_encoder().dimension,
        },
        "images": {
            "strategy": settings.image_strategy.value,
            "provider": pipeline.backend_name,
            "flux_active": pipeline.flux_active,
            "model": settings.flux_model,
        },
        "database": {
            "engine": "postgresql" if settings.uses_postgres else "sqlite",
        },
    }
