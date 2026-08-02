"""Generation, retrieval and selection of layouts, plus image serving."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import FileResponse

from app.api.deps import GenerationRepositoryDep, GenerationServiceDep, SettingsDep
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.schemas.layout import GeneratedLayout, GenerationResponse, TemplateMatch
from app.schemas.requirements import FloorPlanRequirements, GenerationRequest

logger = get_logger(__name__)

router = APIRouter(tags=["generation"])


@router.post(
    "/generate",
    response_model=GenerationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Analyse requirements and generate layout images",
)
def generate(
    payload: GenerationRequest,
    service: GenerationServiceDep,
    repository: GenerationRepositoryDep,
) -> GenerationResponse:
    response = service.generate(
        payload.requirements, variants=payload.variants, seed=payload.seed
    )

    try:
        repository.save(
            response,
            requirements=payload.requirements.model_dump(mode="json"),
            seed=payload.seed or 0,
        )
    except Exception:
        logger.exception("Could not persist session %s", response.session_id)
        response.warnings.append("Results were generated but could not be saved for later.")

    return response


@router.post(
    "/match",
    response_model=list[TemplateMatch],
    summary="Score the knowledge base without generating images",
)
def match(
    requirements: FloorPlanRequirements, service: GenerationServiceDep
) -> list[TemplateMatch]:
    """Fast preview of which templates the agent would work from."""
    return service.preview_matches(requirements)


@router.get(
    "/sessions/{session_id}",
    response_model=GenerationResponse,
    summary="Re-open a previous result set",
)
def get_session(session_id: str, repository: GenerationRepositoryDep) -> GenerationResponse:
    return repository.get_session(session_id)


@router.get(
    "/layouts/{layout_id}",
    response_model=GeneratedLayout,
    summary="One generated layout",
)
def get_layout(layout_id: str, repository: GenerationRepositoryDep) -> GeneratedLayout:
    return repository.get_layout(layout_id)


@router.post(
    "/layouts/{layout_id}/select",
    response_model=GeneratedLayout,
    summary="Mark a layout as the user's choice",
)
def select_layout(layout_id: str, repository: GenerationRepositoryDep) -> GeneratedLayout:
    return repository.select_layout(layout_id)


@router.get("/images/{session_id}/{filename}", summary="Serve a rendered plan")
def get_image(session_id: str, filename: str, settings: SettingsDep) -> FileResponse:
    # Both segments come from the URL, so they are validated before touching
    # the filesystem - no traversal outside the storage directory.
    if not _is_safe(session_id) or not _is_safe(filename) or not filename.endswith(".png"):
        raise ValidationError("Invalid image path")

    path = (settings.storage_path / session_id / filename).resolve()
    if not path.is_relative_to(settings.storage_path.resolve()) or not path.is_file():
        raise NotFoundError("Image not found")

    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _is_safe(segment: str) -> bool:
    return bool(segment) and all(c.isalnum() or c in "-_." for c in segment) and ".." not in segment
