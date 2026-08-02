"""Domain exceptions.

The API layer maps these onto HTTP status codes in one place
(:mod:`app.api.error_handlers`) so services never import ``HTTPException``.
"""

from __future__ import annotations


class FloorPlannerError(Exception):
    """Base class for every error this application raises deliberately."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(FloorPlannerError):
    status_code = 422
    code = "validation_error"


class NotFoundError(FloorPlannerError):
    status_code = 404
    code = "not_found"


class KnowledgeBaseError(FloorPlannerError):
    status_code = 503
    code = "knowledge_base_unavailable"


class LayoutGenerationError(FloorPlannerError):
    status_code = 500
    code = "layout_generation_failed"


class ImageProviderError(FloorPlannerError):
    """Raised when a remote image backend fails.

    Non-fatal by design: the pipeline falls back to the vector renderer.
    """

    status_code = 502
    code = "image_provider_failed"
