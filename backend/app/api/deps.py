"""FastAPI dependency wiring.

Composition happens here and nowhere else: services receive their
collaborators, never construct them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.ai.imaging.pipeline import ImagePipeline, get_image_pipeline
from app.ai.llm.requirement_analyzer import RequirementAnalyzer
from app.ai.retrieval.matcher import TemplateMatcher
from app.ai.retrieval.vector_store import get_vector_store
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.repositories.generation_repository import GenerationRepository
from app.repositories.template_repository import (
    JsonTemplateRepository,
    SqlTemplateRepository,
    TemplateRepository,
)
from app.services.generation_service import GenerationService

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]


def get_template_repository(session: SessionDep, settings: SettingsDep) -> TemplateRepository:
    """Prefer the database; fall back to the JSON files when it is not seeded.

    This is what lets the API serve real results on a fresh checkout before
    ``seed_database.py`` has ever been run.
    """
    sql_repo = SqlTemplateRepository(session)
    try:
        if sql_repo.count() > 0:
            return sql_repo
    except Exception:
        pass
    return JsonTemplateRepository(settings.templates_path)


TemplateRepositoryDep = Annotated[TemplateRepository, Depends(get_template_repository)]


def get_generation_repository(session: SessionDep) -> GenerationRepository:
    return GenerationRepository(session)


GenerationRepositoryDep = Annotated[GenerationRepository, Depends(get_generation_repository)]


def get_matcher(repository: TemplateRepositoryDep) -> TemplateMatcher:
    return TemplateMatcher(repository, get_vector_store())


def get_analyzer() -> RequirementAnalyzer:
    return RequirementAnalyzer()


def get_pipeline() -> ImagePipeline:
    return get_image_pipeline()


def get_generation_service(
    analyzer: Annotated[RequirementAnalyzer, Depends(get_analyzer)],
    matcher: Annotated[TemplateMatcher, Depends(get_matcher)],
    pipeline: Annotated[ImagePipeline, Depends(get_pipeline)],
    settings: SettingsDep,
) -> GenerationService:
    return GenerationService(analyzer, matcher, pipeline, settings)


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]
