"""Read access to the Template Knowledge Base."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import TemplateRepositoryDep
from app.schemas.enums import BHKType, InteriorStyle
from app.schemas.template import FloorPlanTemplate, TemplateSummary

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateSummary], summary="List templates")
def list_templates(
    repository: TemplateRepositoryDep,
    bhk: BHKType | None = Query(default=None),
    style: InteriorStyle | None = Query(default=None),
) -> list[TemplateSummary]:
    templates = repository.list_all()
    if bhk:
        templates = [t for t in templates if t.bhk is bhk]
    if style:
        templates = [t for t in templates if t.style is style]
    return [TemplateSummary.from_template(t) for t in templates]


@router.get("/{template_id}", response_model=FloorPlanTemplate, summary="Full template")
def get_template(template_id: str, repository: TemplateRepositoryDep) -> FloorPlanTemplate:
    return repository.get(template_id)
