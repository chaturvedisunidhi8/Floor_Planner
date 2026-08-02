"""Template retrieval: semantic search + rule-based re-ranking.

FAISS narrows and softly orders the library; the explicit scorer decides. The
two are combined per template rather than sequentially so a template that the
embedding ranks poorly can still win on hard architectural criteria.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.retrieval.scorer import DEFAULT_WEIGHTS, ScoringWeights, SimilarityScorer
from app.ai.retrieval.vector_store import FaissVectorStore
from app.core.logging import get_logger
from app.repositories.template_repository import TemplateRepository
from app.schemas.layout import ScoreBreakdown, TemplateMatch
from app.schemas.requirements import FloorPlanRequirements
from app.schemas.template import FloorPlanTemplate, TemplateSummary

logger = get_logger(__name__)


@dataclass
class ScoredTemplate:
    template: FloorPlanTemplate
    score: float
    breakdown: ScoreBreakdown
    rationale: str


class TemplateMatcher:
    def __init__(
        self,
        repository: TemplateRepository,
        vector_store: FaissVectorStore,
        weights: ScoringWeights = DEFAULT_WEIGHTS,
    ) -> None:
        self._repository = repository
        self._vector_store = vector_store
        self._scorer = SimilarityScorer(weights)

    def match(self, requirements: FloorPlanRequirements, top_k: int) -> list[ScoredTemplate]:
        templates = self._repository.list_all()
        self._vector_store.ensure_ready(templates)

        query = requirements.to_search_text()
        try:
            semantic_scores = self._vector_store.score_all(query)
        except Exception as exc:
            logger.warning("Semantic search unavailable (%s); using rule scores only.", exc)
            semantic_scores = {}

        scored: list[ScoredTemplate] = []
        for template in templates:
            breakdown = self._scorer.breakdown(
                requirements, template, semantic_scores.get(template.id, 0.5)
            )
            scored.append(
                ScoredTemplate(
                    template=template,
                    score=self._scorer.aggregate(breakdown),
                    breakdown=breakdown,
                    rationale=self._scorer.explain(breakdown),
                )
            )

        scored.sort(key=lambda s: (s.score, s.template.id), reverse=True)
        top = scored[: max(1, top_k)]
        logger.info(
            "Top matches: %s",
            ", ".join(f"{s.template.id}={s.score:.3f}" for s in top),
        )
        return top

    @staticmethod
    def to_response(scored: list[ScoredTemplate]) -> list[TemplateMatch]:
        return [
            TemplateMatch(
                template=TemplateSummary.from_template(item.template),
                score=item.score,
                breakdown=item.breakdown,
                rank=rank,
                rationale=item.rationale,
            )
            for rank, item in enumerate(scored, start=1)
        ]
