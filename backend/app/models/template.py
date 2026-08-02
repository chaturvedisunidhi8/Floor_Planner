"""ORM mapping for the Template Knowledge Base."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableJSON, timestamp_column
from app.schemas.template import FloorPlanTemplate


class TemplateRecord(Base):
    """A knowledge-base template.

    The full document lives in ``payload`` (single source of truth); the scalar
    columns are denormalised copies used for SQL pre-filtering before the
    vector search runs.
    """

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    bhk: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    style: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    plot_width_ft: Mapped[float] = mapped_column(Float, nullable=False)
    plot_length_ft: Mapped[float] = mapped_column(Float, nullable=False)
    plot_area_sqft: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    built_up_sqft: Mapped[float] = mapped_column(Float, nullable=False)
    bedroom_count: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    attached_bathroom_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    common_bathroom_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_parking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_balcony: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    created_at: Mapped[datetime] = timestamp_column(nullable=False)

    __table_args__ = (Index("ix_templates_bhk_area", "bhk", "plot_area_sqft"),)

    @classmethod
    def from_domain(cls, template: FloorPlanTemplate) -> TemplateRecord:
        return cls(
            id=template.id,
            name=template.name,
            bhk=template.bhk.value,
            style=template.style.value,
            plot_width_ft=template.plot_width_ft,
            plot_length_ft=template.plot_length_ft,
            plot_area_sqft=template.plot_area,
            built_up_sqft=template.built_up_area,
            bedroom_count=template.bedroom_count,
            attached_bathroom_count=template.attached_bathroom_count,
            common_bathroom_count=template.common_bathroom_count,
            has_parking=template.has_parking,
            has_balcony=template.has_balcony,
            description=template.description,
            embedding_text=template.to_embedding_text(),
            payload=template.model_dump(mode="json"),
        )

    def to_domain(self) -> FloorPlanTemplate:
        return FloorPlanTemplate.model_validate(self.payload)
