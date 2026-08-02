"""Persistence for generation sessions and their layouts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.generation import GeneratedLayoutRecord, GenerationSession
from app.schemas.layout import GeneratedLayout, GenerationResponse


class GenerationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, response: GenerationResponse, requirements: dict, seed: int) -> None:
        record = GenerationSession(
            id=response.session_id,
            requirements=requirements,
            analysis=response.analysis,
            matches=[m.model_dump(mode="json") for m in response.matches],
            requirement_summary=response.requirement_summary,
            warnings=response.warnings,
            seed=seed,
            created_at=response.created_at,
        )
        for position, layout in enumerate(response.layouts):
            record.layouts.append(
                GeneratedLayoutRecord(
                    id=layout.id,
                    position=position,
                    name=layout.name,
                    description=layout.description,
                    source_template_id=layout.source_template_id,
                    match_score=layout.match_score,
                    render_mode=layout.render_mode,
                    image_path=layout.image_url,
                    payload=layout.model_dump(mode="json"),
                )
            )
        self._session.merge(record)
        self._session.flush()

    def get_session(self, session_id: str) -> GenerationResponse:
        record = self._session.get(GenerationSession, session_id)
        if record is None:
            raise NotFoundError(f"Generation session '{session_id}' does not exist")
        return GenerationResponse(
            session_id=record.id,
            created_at=record.created_at,
            requirement_summary=record.requirement_summary,
            analysis=record.analysis,
            matches=record.matches,  # type: ignore[arg-type]  - validated on the way in
            layouts=[GeneratedLayout.model_validate(layout.payload) for layout in record.layouts],
            warnings=record.warnings,
        )

    def get_layout(self, layout_id: str) -> GeneratedLayout:
        record = self._session.get(GeneratedLayoutRecord, layout_id)
        if record is None:
            raise NotFoundError(f"Layout '{layout_id}' does not exist")
        return GeneratedLayout.model_validate(record.payload)

    def select_layout(self, layout_id: str) -> GeneratedLayout:
        """Mark one layout as the user's favourite, clearing any previous pick."""
        record = self._session.get(GeneratedLayoutRecord, layout_id)
        if record is None:
            raise NotFoundError(f"Layout '{layout_id}' does not exist")

        siblings = self._session.scalars(
            select(GeneratedLayoutRecord).where(
                GeneratedLayoutRecord.session_id == record.session_id
            )
        ).all()
        for sibling in siblings:
            sibling.is_selected = sibling.id == layout_id
        self._session.flush()
        return GeneratedLayout.model_validate(record.payload)

    def recent_sessions(self, limit: int = 20) -> list[GenerationSession]:
        return list(
            self._session.scalars(
                select(GenerationSession)
                .order_by(GenerationSession.created_at.desc())
                .limit(limit)
            ).all()
        )
