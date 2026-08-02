"""ORM mappings for generation sessions and the layouts they produced."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PortableJSON, timestamp_column


class GenerationSession(Base):
    """One submission of the requirement wizard."""

    __tablename__ = "generation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requirements: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    analysis: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    matches: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    requirement_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    warnings: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = timestamp_column(nullable=False, index=True)

    layouts: Mapped[list[GeneratedLayoutRecord]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="GeneratedLayoutRecord.position",
    )


class GeneratedLayoutRecord(Base):
    """One of the 3-4 image variations shown in the gallery."""

    __tablename__ = "generated_layouts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("generation_sessions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_template_id: Mapped[str] = mapped_column(String(16), nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    render_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="vector")
    image_path: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = timestamp_column(nullable=False)

    session: Mapped[GenerationSession] = relationship(back_populates="layouts")
