"""The orchestrator: requirements in, gallery of layouts out.

Implements the workflow from the specification end to end.

    requirements
        -> LLM requirement analysis          (app.ai.llm)
        -> embed + FAISS search + re-ranking (app.ai.retrieval)
        -> top N matching templates
        -> geometry engine, one variation per template
        -> prompt builder + FLUX / vector render
        -> persisted gallery response

Each stage is injected, so the service is unit-testable without a database,
an LLM key or a network connection.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

from app.ai.imaging.pipeline import ImagePipeline
from app.ai.llm.requirement_analyzer import RequirementAnalysis, RequirementAnalyzer
from app.ai.retrieval.matcher import ScoredTemplate, TemplateMatcher
from app.core.config import Settings
from app.core.exceptions import LayoutGenerationError
from app.core.logging import get_logger
from app.geometry.layout_engine import LayoutEngine, LayoutPlan
from app.schemas.enums import RoomType
from app.schemas.layout import GeneratedLayout, GenerationResponse, LayoutRoom, TemplateMatch
from app.schemas.requirements import FloorPlanRequirements

logger = get_logger(__name__)

#: Names given to the variations, in order.
VARIANT_NAMES = (
    "Classic Arrangement",
    "Mirrored Flow",
    "Open Core",
    "Compact Wing",
    "Extended Living",
    "Split Zone",
    "Corner Suite",
    "Garden Facing",
)


class GenerationService:
    def __init__(
        self,
        analyzer: RequirementAnalyzer,
        matcher: TemplateMatcher,
        image_pipeline: ImagePipeline,
        settings: Settings,
    ) -> None:
        self._analyzer = analyzer
        self._matcher = matcher
        self._images = image_pipeline
        self._settings = settings

    def generate(
        self,
        requirements: FloorPlanRequirements,
        *,
        variants: int | None = None,
        seed: int | None = None,
    ) -> GenerationResponse:
        session_id = str(uuid.uuid4())
        base_seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        wanted = variants or self._settings.variants_per_request
        warnings: list[str] = []

        logger.info(
            "Session %s: %s on %s (seed %d, %d variants)",
            session_id[:8],
            requirements.bhk.value,
            requirements.plot.describe(),
            base_seed,
            wanted,
        )

        analysis = self._analyzer.analyze(requirements)
        matches = self._match(requirements, analysis)
        layouts = self._build_layouts(
            requirements, matches, session_id, base_seed, wanted, warnings
        )

        if not layouts:
            raise LayoutGenerationError(
                "No valid layout could be produced for these requirements. "
                "Try a larger plot or fewer rooms."
            )

        return GenerationResponse(
            session_id=session_id,
            created_at=datetime.now(UTC),
            requirement_summary=analysis.summary,
            analysis=dict(analysis),
            matches=TemplateMatcher.to_response(matches),
            layouts=layouts,
            warnings=warnings,
        )

    def preview_matches(
        self, requirements: FloorPlanRequirements, top_k: int | None = None
    ) -> list[TemplateMatch]:
        """Score the knowledge base without generating any images."""
        scored = self._matcher.match(requirements, top_k or self._settings.top_k_templates)
        return TemplateMatcher.to_response(scored)

    # --- Stages -----------------------------------------------------------
    def _match(
        self, requirements: FloorPlanRequirements, analysis: RequirementAnalysis
    ) -> list[ScoredTemplate]:
        matches = self._matcher.match(requirements, self._settings.top_k_templates)
        if not matches:
            raise LayoutGenerationError("The template knowledge base returned no candidates")
        return matches

    def _build_layouts(
        self,
        requirements: FloorPlanRequirements,
        matches: list[ScoredTemplate],
        session_id: str,
        base_seed: int,
        wanted: int,
        warnings: list[str],
    ) -> list[GeneratedLayout]:
        engine = LayoutEngine(requirements)
        layouts: list[GeneratedLayout] = []

        # One variation per matched template first, so the gallery shows
        # genuinely different plans rather than four takes on the same one.
        # Only if there are fewer templates than variants do we revisit the
        # best match with a different variation operator.
        sources = list(matches)
        while len(sources) < wanted:
            sources.append(matches[len(sources) % len(matches)])

        for index, match in enumerate(sources[:wanted]):
            seed = base_seed + index * 977  # prime stride keeps the RNG streams apart
            try:
                plan = engine.generate(match.template, seed=seed, variation_index=index)
            except Exception as exc:
                logger.exception("Layout %d from %s failed", index, match.template.id)
                warnings.append(f"Skipped one variation ({type(exc).__name__}: {exc})")
                continue

            layout = self._render_layout(
                plan, requirements, match, session_id, index, warnings
            )
            layouts.append(layout)

        if requirements.vastu.is_active:
            # A client who asked for Vastu is ranking on it, so lead with the
            # plan that follows it best rather than with the closest template
            # match. Sorting is stable, so match order still breaks ties.
            layouts.sort(key=lambda layout: layout.vastu_score or 0.0, reverse=True)

        return layouts

    def _render_layout(
        self,
        plan: LayoutPlan,
        requirements: FloorPlanRequirements,
        match: ScoredTemplate,
        session_id: str,
        index: int,
        warnings: list[str],
    ) -> GeneratedLayout:
        layout_id = f"{session_id[:8]}-{index + 1}"
        name = VARIANT_NAMES[index % len(VARIANT_NAMES)]
        plot_label = f"{plan.plot_width:g} x {plan.plot_length:g} ft"
        subtitle = f"{requirements.bhk.value}  |  {plot_label}  |  {requirements.style.label}"

        destination = self._settings.storage_path / session_id / f"{layout_id}.png"
        rendered = self._images.generate(
            plan,
            requirements,
            destination=destination,
            title=name,
            subtitle=subtitle,
        )
        if rendered.warning:
            warnings.append(rendered.warning)

        relative = f"{session_id}/{destination.name}"
        return GeneratedLayout(
            id=layout_id,
            name=name,
            bhk=requirements.bhk,
            style=requirements.style,
            plot_width_ft=plan.plot_width,
            plot_length_ft=plan.plot_length,
            plot_size_label=plot_label,
            built_up_sqft=plan.built_up_sqft,
            description=self._describe(plan, requirements, match),
            rooms=[
                LayoutRoom(
                    type=r.type,
                    name=r.name,
                    x=round(r.x, 2),
                    y=round(r.y, 2),
                    width=round(r.width, 2),
                    height=round(r.height, 2),
                )
                for r in plan.rooms
            ],
            image_url=f"{self._settings.api_prefix}/images/{relative}",
            source_template_id=match.template.id,
            source_template_name=match.template.name,
            match_score=match.score,
            render_mode=rendered.mode,
            validation_warnings=plan.warnings,
            seed=plan.seed,
            vastu_score=plan.vastu.score if plan.vastu else None,
            vastu_notes=plan.vastu.notes if plan.vastu else [],
        )

    @staticmethod
    def _describe(
        plan: LayoutPlan, requirements: FloorPlanRequirements, match: ScoredTemplate
    ) -> str:
        """The short blurb on each gallery card."""
        living = next((r for r in plan.rooms if r.type is RoomType.LIVING_ROOM), None)
        master = next((r for r in plan.rooms if r.type is RoomType.MASTER_BEDROOM), None)

        parts = [
            f"{plan.variation} of {match.template.name.lower()}, "
            f"adapted to your {plan.plot_width:g} x {plan.plot_length:g} ft plot"
        ]
        if living:
            parts.append(f"{living.width:g} x {living.height:g} ft living room")
        if master:
            parts.append(f"{master.width:g} x {master.height:g} ft master bedroom")
        parts.append(f"{plan.built_up_sqft:,.0f} sq ft built-up across {plan.room_count} spaces")
        return ". ".join(parts).capitalize() + "."
