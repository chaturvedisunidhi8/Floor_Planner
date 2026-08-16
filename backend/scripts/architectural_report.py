"""Architectural quality report over the solver corpus.

The benchmark reports geometry (area error, connectivity, feasibility). This
script reports the *architecture* - the axes the adaptive search was built to
move: how often the corridor forms one usable spine, how often doors and
windows respect their clearances, how strongly the wet zone and the bedrooms
cohere, how much daylight the habitable rooms get. Every number is a mean over
the feasible solver plans of the corpus, so the report shows what a typical
winning plan reads like, not just the worst case.

Run with::

    python scripts/architectural_report.py                      # full corpus
    python scripts/architectural_report.py --briefs "3BHK standard 30x45"
    python scripts/architectural_report.py --report benchmarks/architectural_report.json

The JSON report carries, per brief and for the corpus, the mean of every
architectural axis (``None`` when no plan of that kind exists) alongside the
score and the distribution of winning search candidates.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.geometry.layout_engine import LayoutEngine
from app.geometry.quality import quality_metrics
from app.repositories.template_repository import JsonTemplateRepository
from scripts.benchmark import _briefs


@dataclass
class Metrics:
    """Aggregated architectural measurements over a set of plans."""

    name: str = ""
    plans: int = 0
    scores: list[float] = field(default_factory=list)
    corridor_fragmentation: list[float] = field(default_factory=list)
    corridor_min_width: list[float] = field(default_factory=list)
    door_corner_violations: int = 0
    door_spacing_violations: int = 0
    opposing_door_pairs: int = 0
    window_corner_violations: int = 0
    window_door_violations: int = 0
    slender_share: list[float] = field(default_factory=list)
    social_share: list[float] = field(default_factory=list)
    attached_bedroom_share: list[float] = field(default_factory=list)
    common_bedroom_share: list[float] = field(default_factory=list)
    bedroom_from_circulation: list[float] = field(default_factory=list)
    private_zone_share: list[float] = field(default_factory=list)
    cross_ventilation: list[float] = field(default_factory=list)
    uncovered_fraction: list[float] = field(default_factory=list)
    balcony_without_habitable: int = 0
    winners: Counter = field(default_factory=Counter)
    times: list[float] = field(default_factory=list)

    def merge(self, other: Metrics) -> None:
        self.plans += other.plans
        self.scores.extend(other.scores)
        self.corridor_fragmentation.extend(other.corridor_fragmentation)
        self.corridor_min_width.extend(other.corridor_min_width)
        self.door_corner_violations += other.door_corner_violations
        self.door_spacing_violations += other.door_spacing_violations
        self.opposing_door_pairs += other.opposing_door_pairs
        self.window_corner_violations += other.window_corner_violations
        self.window_door_violations += other.window_door_violations
        self.slender_share.extend(other.slender_share)
        self.social_share.extend(other.social_share)
        self.attached_bedroom_share.extend(other.attached_bedroom_share)
        self.common_bedroom_share.extend(other.common_bedroom_share)
        self.bedroom_from_circulation.extend(other.bedroom_from_circulation)
        self.private_zone_share.extend(other.private_zone_share)
        self.cross_ventilation.extend(other.cross_ventilation)
        self.uncovered_fraction.extend(other.uncovered_fraction)
        self.balcony_without_habitable += other.balcony_without_habitable
        self.winners.update(other.winners)
        self.times.extend(other.times)


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _fmt(value: float | None, spec: str) -> str:
    return "  n/a" if value is None else f"{value:{spec}}"


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:.2%}"


def _collect(brief, template, engine, seed, variation, budget, candidates, m: Metrics) -> None:
    """One attempt: solve, measure the winner, fold the numbers into ``m``."""
    started = time.perf_counter()
    plan = engine.generate_solver(
        template,
        seed=seed,
        variation_index=variation,
        time_limit=budget,
        topology_candidates=candidates,
    )
    m.times.append(time.perf_counter() - started)
    if plan.status != "feasible":
        return
    m.plans += 1
    if plan.quality_score is not None:
        m.scores.append(plan.quality_score)
    if plan.topology_search:
        survivors = (
            e
            for e in plan.topology_search
            if e["status"] == "feasible" and e.get("score") is not None
        )
        winner = max(survivors, key=lambda e: e["score"], default=None)
        if winner is not None:
            m.winners[winner["label"]] += 1

    metrics = quality_metrics(plan)
    if metrics.corridor_fragmentation is not None:
        m.corridor_fragmentation.append(metrics.corridor_fragmentation)
    if metrics.corridor_min_width is not None:
        m.corridor_min_width.append(metrics.corridor_min_width)
    m.door_corner_violations += metrics.door_corner_violations
    m.door_spacing_violations += metrics.door_spacing_violations
    m.opposing_door_pairs += metrics.opposing_door_pairs
    m.window_corner_violations += metrics.window_corner_violations
    m.window_door_violations += metrics.window_door_violations
    if metrics.bathroom_count:
        m.slender_share.append(metrics.slender_bathrooms / metrics.bathroom_count)
        m.social_share.append(metrics.bathroom_social_walls / metrics.bathroom_count)
    if metrics.attached_bath_bedroom_share is not None:
        m.attached_bedroom_share.append(metrics.attached_bath_bedroom_share)
    if metrics.common_bath_bedroom_share is not None:
        m.common_bedroom_share.append(metrics.common_bath_bedroom_share)
    if metrics.bedroom_from_circulation is not None:
        m.bedroom_from_circulation.append(metrics.bedroom_from_circulation)
    if metrics.private_zone_share is not None:
        m.private_zone_share.append(metrics.private_zone_share)
    m.cross_ventilation.append(
        metrics.cross_ventilated / len(plan.rooms) if plan.rooms else 0.0
    )
    m.uncovered_fraction.append(metrics.uncovered_fraction)
    m.balcony_without_habitable += metrics.balcony_without_habitable


def _summary(m: Metrics) -> dict:
    return {
        "plans": m.plans,
        "score_avg": _mean(m.scores),
        "corridor_fragmentation": _mean(m.corridor_fragmentation),
        "corridor_min_width": _mean(m.corridor_min_width),
        "door_corner_violations_per_plan": _fraction(m.door_corner_violations, m.plans),
        "door_spacing_violations_per_plan": _fraction(m.door_spacing_violations, m.plans),
        "opposing_door_pairs_per_plan": _fraction(m.opposing_door_pairs, m.plans),
        "window_corner_violations_per_plan": _fraction(
            m.window_corner_violations, m.plans
        ),
        "window_door_violations_per_plan": _fraction(m.window_door_violations, m.plans),
        "slender_bathrooms_share": _mean(m.slender_share),
        "bathroom_social_walls_share": _mean(m.social_share),
        "attached_bath_bedroom_share": _mean(m.attached_bedroom_share),
        "common_bath_bedroom_share": _mean(m.common_bedroom_share),
        "bedroom_from_circulation": _mean(m.bedroom_from_circulation),
        "private_zone_share": _mean(m.private_zone_share),
        "cross_ventilated_rooms_share": _mean(m.cross_ventilation),
        "uncovered_fraction": _mean(m.uncovered_fraction),
        "balcony_without_habitable": m.balcony_without_habitable,
        "winners": dict(m.winners),
        "time_ms": _mean(m.times) * 1000 if m.times else None,
    }


def _print_row(m: Metrics, width: int = 88) -> None:
    print(f"{m.name:<{width}.{width}s}")
    print(
        f"    plans {m.plans:4d}   score {_fmt(_mean(m.scores), '6.1f')}   "
        f"corridor frag {_fmt(_mean(m.corridor_fragmentation), '5.2f')}   "
        f"min-width {_fmt(_mean(m.corridor_min_width), '4.1f')}"
    )
    print(
        f"    doors: corner {m.door_corner_violations}/{m.plans}  "
        f"spacing {m.door_spacing_violations}/{m.plans}  "
        f"opposing {m.opposing_door_pairs}/{m.plans}   "
        f"windows: corner {m.window_corner_violations}/{m.plans}  "
        f"door {m.window_door_violations}/{m.plans}"
    )
    print(
        f"    wet: slender {_pct(_mean(m.slender_share))}  social {_pct(_mean(m.social_share))}  "
        f"attached-br {_pct(_mean(m.attached_bedroom_share))}  "
        f"common-br {_pct(_mean(m.common_bedroom_share))}"
    )
    print(
        f"    zones: br-from-circ {_pct(_mean(m.bedroom_from_circulation))}  "
        f"private-zone {_pct(_mean(m.private_zone_share))}   "
        f"daylight {_pct(_mean(m.cross_ventilation))}   "
        f"uncovered {_pct(_mean(m.uncovered_fraction))}   "
        f"balcony-without {m.balcony_without_habitable}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", type=int, default=2)
    parser.add_argument("--solver-budget", type=float, default=1.5)
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--briefs", type=str, default="", help="comma-separated subset")
    parser.add_argument("--templates", type=str, default="", help="comma-separated ids")
    parser.add_argument("--report", type=str, default="", help="JSON report path")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    repository = JsonTemplateRepository(settings.templates_path)
    template_ids = [t.strip() for t in args.templates.split(",") if t.strip()]

    total = Metrics(name=f"architectural quality ({args.candidates} candidates)")
    per_brief: dict[str, dict] = {}
    for name, requirements, infeasible in _briefs():
        if args.briefs and name not in args.briefs:
            continue
        run = Metrics(name=name)
        engine = LayoutEngine(requirements)
        for template_id in template_ids or [f"TPL-{i:03d}" for i in range(1, 21)]:
            template = repository.get(template_id)
            for variation in range(args.variants):
                _collect(
                    (name, requirements, infeasible),
                    template,
                    engine,
                    seed=100 + variation,
                    variation=variation,
                    budget=args.solver_budget,
                    candidates=args.candidates,
                    m=run,
                )
        _print_row(run)
        total.merge(run)
        per_brief[name] = _summary(run)

    print(f"\n=== TOTALS  ({args.candidates} candidates, {args.variants} seeds x {len(template_ids) or 20} templates) ===")  # noqa: E501
    _print_row(total, width=110)
    winners = ", ".join(f"{k}: {v}" for k, v in total.winners.most_common())
    print(f"    winners by candidate: {winners}")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "settings": {
                        "candidates": args.candidates,
                        "variants": args.variants,
                        "solver_budget": args.solver_budget,
                    },
                    "per_brief": per_brief,
                    "total": _summary(total),
                },
                indent=2,
                sort_keys=True,
            )
        )
        print(f"\nReport written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
