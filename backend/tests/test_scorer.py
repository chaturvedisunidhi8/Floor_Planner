"""Retrieval scoring: the criteria the specification names explicitly."""

from __future__ import annotations

import pytest

from app.ai.retrieval.matcher import TemplateMatcher
from app.ai.retrieval.scorer import DEFAULT_WEIGHTS, SimilarityScorer
from app.ai.retrieval.vector_store import get_vector_store
from app.schemas.enums import BHKType, InteriorStyle, RoomType
from app.schemas.requirements import BathroomRequirements, PlotDetails
from tests.conftest import make_requirements


@pytest.fixture(scope="module")
def scorer() -> SimilarityScorer:
    return SimilarityScorer()


def test_weights_are_normalised(scorer: SimilarityScorer) -> None:
    assert DEFAULT_WEIGHTS.total() == pytest.approx(1.0, abs=0.001)


def test_exact_bedroom_match_scores_one(scorer, repository) -> None:
    template = repository.get("TPL-010")  # 3BHK
    assert scorer.score_bedrooms(make_requirements(bhk=BHKType.BHK3), template) == 1.0


def test_missing_a_bedroom_is_penalised_harder_than_a_spare_one(scorer, repository) -> None:
    template = repository.get("TPL-010")  # 3BHK
    too_few = scorer.score_bedrooms(make_requirements(bhk=BHKType.BHK4), template)
    too_many = scorer.score_bedrooms(make_requirements(bhk=BHKType.BHK2), template)
    assert too_few < too_many


def test_plot_score_rewards_a_close_match(scorer, repository) -> None:
    template = repository.get("TPL-010")  # 36 x 50
    close = make_requirements(plot=PlotDetails(width_ft=36, length_ft=50))
    far = make_requirements(plot=PlotDetails(width_ft=18, length_ft=22))
    assert scorer.score_plot(close, template) > 0.9
    assert scorer.score_plot(far, template) < scorer.score_plot(close, template)


def test_plot_score_tolerates_a_rotated_plot(scorer, repository) -> None:
    """A 36x50 template suits a 50x36 plot; only the orientation differs."""
    template = repository.get("TPL-010")
    rotated = make_requirements(plot=PlotDetails(width_ft=50, length_ft=36))
    assert scorer.score_plot(rotated, template) > 0.75


def test_substitute_rooms_earn_partial_credit(scorer, repository) -> None:
    """A template with a living room partly satisfies a request for dining."""
    template = repository.get("TPL-003")  # no dining room
    requirements = make_requirements(
        rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN, RoomType.DINING_ROOM]
    )
    score = scorer.score_required_rooms(requirements, template)
    assert 0 < score < 1.0


def test_feature_scoring_is_asymmetric(scorer) -> None:
    """Missing what was asked for is worse than having a spare."""
    assert scorer.score_feature(wanted=True, present=True) == 1.0
    assert scorer.score_feature(wanted=False, present=False) == 1.0
    assert scorer.score_feature(wanted=False, present=True) == 0.7
    assert scorer.score_feature(wanted=True, present=False) == 0.0


def test_related_styles_score_above_unrelated_ones(scorer, repository) -> None:
    modern_template = repository.get("TPL-002")  # modern
    minimal = scorer.score_style(make_requirements(style=InteriorStyle.MINIMAL), modern_template)
    traditional = scorer.score_style(
        make_requirements(style=InteriorStyle.TRADITIONAL), modern_template
    )
    assert 1.0 > minimal > traditional


def test_aggregate_stays_within_bounds(scorer, repository, requirements) -> None:
    for template in repository.list_all():
        breakdown = scorer.breakdown(requirements, template, semantic_score=0.5)
        assert 0.0 <= scorer.aggregate(breakdown) <= 1.0


def test_explain_produces_readable_text(scorer, repository, requirements) -> None:
    breakdown = scorer.breakdown(requirements, repository.get("TPL-010"), 0.8)
    rationale = scorer.explain(breakdown)
    assert isinstance(rationale, str)
    assert len(rationale) > 10


# --- Matcher --------------------------------------------------------------
def test_matcher_returns_the_requested_number_ranked(repository, requirements) -> None:
    matches = TemplateMatcher(repository, get_vector_store()).match(requirements, top_k=5)
    assert len(matches) == 5
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)


def test_matcher_prefers_the_right_bhk(repository) -> None:
    """A 4BHK brief should not be answered mainly with 1BHK cottages."""
    requirements = make_requirements(
        bhk=BHKType.BHK4,
        plot=PlotDetails(width_ft=45, length_ft=55),
        bathrooms=BathroomRequirements(attached_count=3, common_count=1),
    )
    matches = TemplateMatcher(repository, get_vector_store()).match(requirements, top_k=3)
    assert any(m.template.bhk is BHKType.BHK4 for m in matches)
    assert matches[0].template.bhk in {BHKType.BHK4, BHKType.BHK3}


def test_matcher_prefers_a_similar_plot_size(repository) -> None:
    small = make_requirements(bhk=BHKType.BHK1, plot=PlotDetails(width_ft=22, length_ft=30))
    matches = TemplateMatcher(repository, get_vector_store()).match(small, top_k=3)
    assert matches[0].template.plot_width_ft * matches[0].template.plot_length_ft < 1400


def test_response_projection_numbers_the_ranks(repository, requirements) -> None:
    scored = TemplateMatcher(repository, get_vector_store()).match(requirements, top_k=4)
    response = TemplateMatcher.to_response(scored)
    assert [m.rank for m in response] == [1, 2, 3, 4]
    assert all(m.rationale for m in response)
