"""Data access for the Template Knowledge Base.

Two implementations behind one protocol: the database-backed repository used in
production and a JSON-file repository that lets the whole AI pipeline run before
Postgres is provisioned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import KnowledgeBaseError, NotFoundError
from app.core.logging import get_logger
from app.models.template import TemplateRecord
from app.schemas.template import FloorPlanTemplate

logger = get_logger(__name__)


class TemplateRepository(Protocol):
    def list_all(self) -> list[FloorPlanTemplate]: ...

    def get(self, template_id: str) -> FloorPlanTemplate: ...

    def count(self) -> int: ...


class JsonTemplateRepository:
    """Reads ``data/templates/TPL-XXX.json``. Cached after the first load."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._cache: dict[str, FloorPlanTemplate] | None = None

    def _load(self) -> dict[str, FloorPlanTemplate]:
        if self._cache is not None:
            return self._cache

        files = sorted(self._directory.glob("TPL-*.json"))
        if not files:
            raise KnowledgeBaseError(
                f"No templates found in {self._directory}. "
                "Run `python scripts/author_templates.py` first."
            )

        templates: dict[str, FloorPlanTemplate] = {}
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                template = FloorPlanTemplate.model_validate(data)
            except Exception as exc:
                logger.error("Skipping malformed template %s: %s", path.name, exc)
                continue
            templates[template.id] = template

        if not templates:
            raise KnowledgeBaseError("Every template file failed validation.")

        logger.info("Loaded %d templates from %s", len(templates), self._directory)
        self._cache = templates
        return templates

    def list_all(self) -> list[FloorPlanTemplate]:
        return list(self._load().values())

    def get(self, template_id: str) -> FloorPlanTemplate:
        try:
            return self._load()[template_id]
        except KeyError:
            raise NotFoundError(f"Template '{template_id}' does not exist") from None

    def count(self) -> int:
        return len(self._load())


class SqlTemplateRepository:
    """Reads templates from PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[FloorPlanTemplate]:
        records = self._session.scalars(select(TemplateRecord).order_by(TemplateRecord.id)).all()
        return [r.to_domain() for r in records]

    def get(self, template_id: str) -> FloorPlanTemplate:
        record = self._session.get(TemplateRecord, template_id)
        if record is None:
            raise NotFoundError(f"Template '{template_id}' does not exist")
        return record.to_domain()

    def count(self) -> int:
        return self._session.query(TemplateRecord).count()

    def upsert_many(self, templates: list[FloorPlanTemplate]) -> int:
        for template in templates:
            record = TemplateRecord.from_domain(template)
            self._session.merge(record)
        self._session.flush()
        return len(templates)
