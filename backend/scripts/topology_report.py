"""The topology-search milestone report: before vs after in one run.

Every solver plan now carries its own search trail
(``LayoutPlan.topology_search``): one entry per candidate, in solve order, with
the label, the solver/gate verdict, the architectural score for survivors and
the validation errors that pruned a candidate. This script replays the corpus
and aggregates that trail so the milestone can be judged on numbers:

* **candidates**       candidates actually attempted per brief (the ``N``).
* **unique topologies**  distinct candidate labels that produced a plan - how
  many materially different arrangements the search actually surfaced.
* **pruned**           candidates the solver solved but the strict gate
  rejected - the number of ``pruned`` entries, and the share of attempts where
  at least one candidate was pruned.
* **base score**       average score of the base programme's own plan (the
  "before" the search, since candidate 0 is always the base).
* **best score**       average score of the plan the search actually returned
  (the "after"). The delta is the search's payoff.
* **unique layouts**   distinct final geometries (``(type, x, y, w, h)``
  signatures) - the diversity the search buys.
* the hard metrics the engine is not allowed to regress: connectivity,
  feasibility, overlap, area error, dimension error.

Run with::

    python scripts/topology_report.py                       # full corpus
    python scripts/topology_report.py --candidates 3        # tune the search
    python scripts/topology_report.py --candidates 1        # base-only baseline
    python scripts/topology_report.py --report out.json     # machine-readable

A ``--candidates 1`` run is exactly the pre-search engine, so the before/after
comparison can use this script twice - the base column and the best column are
also reported inside a single run, which is usually enough.
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
from app.repositories.template_repository import JsonTemplateRepository
from scripts.benchmark import (
    _area_error,
    _briefs,
    _connectivity,
    _dim_error,
    _has_overlap,
)


@dataclass
class Run:
    """Aggregated search metrics over every (brief, template, seed) attempt."""

    name: str = ""
    plans: int = 0
    candidates_attempted: int = 0
    candidate_labels: set[str] = field(default_factory=set)
    winner_labels: Counter = field(default_factory=Counter)
    pruned: int = 0
    pruned_attempts: int = 0
    base_scores: list[float] = field(default_factory=list)
    best_scores: list[float] = field(default_factory=list)
    layouts: set[str] = field(default_factory=set)
    area_errors: list[float] = field(default_factory=list)
    dim_errors: list[float] = field(default_factory=list)
    connected: list[float] = field(default_factory=list)
    overlap_count: int = 0
    feasibility_total: int = 0
    feasibility_correct: int = 0
    times: list[float] = field(default_factory=list)

    def merge(self, other: Run) -> None:
        self.plans += other.plans
        self.candidates_attempted += other.candidates_attempted
        self.candidate_labels.update(other.candidate_labels)
        self.winner_labels.update(other.winner_labels)
        self.pruned += other.pruned
        self.pruned_attempts += other.pruned_attempts
        self.base_scores.extend(other.base_scores)
        self.best_scores.extend(other.best_scores)
        self.layouts.update(other.layouts)
        self.area_errors.extend(other.area_errors)
        self.dim_errors.extend(other.dim_errors)
        self.connected.extend(other.connected)
        self.overlap_count += other.overlap_count
        self.feasibility_total += other.feasibility_total
        self.feasibility_correct += other.feasibility_correct
        self.times.extend(other.times)

    @property
    def overlap_rate(self) -> float:
        return self.overlap_count / self.plans if self.plans else 0.0

    @property
    def feasibility(self) -> float | None:
        if not self.feasibility_total:
            return None
        return self.feasibility_correct / self.feasibility_total

    @property
    def pruned_share(self) -> float:
        return self.pruned_attempts / self.plans if self.plans else 0.0


def _signature(plan) -> str:
    return "|".join(
        f"{r.type}:{r.x:.1f},{r.y:.1f},{r.width:.1f},{r.height:.1f}" for r in plan.rooms
    )


def _aggregate(brief, template, engine, seed, variation, budget, candidates, run: Run) -> None:
    """One attempt: generate, read the search trail, fold it into ``run``."""
    requirements = brief[1]
    started = time.perf_counter()
    plan = engine.generate_solver(
        template,
        seed=seed,
        variation_index=variation,
        time_limit=budget,
        topology_candidates=candidates,
    )
    elapsed = time.perf_counter() - started
    run.times.append(elapsed)
    run.plans += 1
    run.candidates_attempted += max(1, len(plan.topology_search or []))
    if plan.topology_search:
        run.candidate_labels.update(e["label"] for e in plan.topology_search)
        run.pruned += sum(1 for e in plan.topology_search if e["status"] == "pruned")
        if any(e["status"] == "pruned" for e in plan.topology_search):
            run.pruned_attempts += 1
        for entry in plan.topology_search:
            if entry["label"] == "Base" and entry.get("score") is not None:
                run.base_scores.append(entry["score"])

    if plan.status != "feasible":
        run.feasibility_total += 1
        if brief[2]:
            run.feasibility_correct += 1
        return
    run.feasibility_total += 1
    if not brief[2]:
        run.feasibility_correct += 1
    run.area_errors.append(_area_error(plan, requirements))
    run.dim_errors.append(_dim_error(plan, requirements))
    run.connected.append(_connectivity(plan))
    if _has_overlap(plan):
        run.overlap_count += 1
    if plan.quality_score is not None:
        run.best_scores.append(plan.quality_score)
    run.layouts.add(_signature(plan))
    if plan.topology_search:
        survivors = (
            e
            for e in plan.topology_search
            if e["status"] == "feasible" and e.get("score") is not None
        )
        winner = max(survivors, key=lambda e: e["score"], default=None)
        if winner is not None:
            run.winner_labels[winner["label"]] += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates",
        type=int,
        default=None,
        help="topologies per attempt; default is the settings value",
    )
    parser.add_argument("--variants", type=int, default=2, help="seeds per brief/template")
    parser.add_argument("--solver-budget", type=float, default=1.5)
    parser.add_argument(
        "--briefs", type=str, default="", help="comma-separated subset of brief names"
    )
    parser.add_argument("--templates", type=str, default="", help="comma-separated template ids")
    parser.add_argument("--report", type=str, default="", help="path for the JSON report")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    repository = JsonTemplateRepository(settings.templates_path)
    candidates = args.candidates if args.candidates is not None else settings.topology_candidates
    template_ids = [t.strip() for t in args.templates.split(",") if t.strip()]

    total = Run(name=f"solver search ({candidates} candidates)")
    per_brief: dict[str, dict] = {}
    for name, requirements, infeasible in _briefs():
        if args.briefs and name not in args.briefs:
            continue
        run = Run(name=name)
        for template_id in template_ids or [f"TPL-{i:03d}" for i in range(1, 21)]:
            template = repository.get(template_id)
            engine = LayoutEngine(requirements)
            for variation in range(args.variants):
                _aggregate(
                    (name, requirements, infeasible),
                    template,
                    engine,
                    seed=100 + variation,
                    variation=variation,
                    budget=args.solver_budget,
                    candidates=candidates,
                    run=run,
                )
        _print_row(run, candidates)
        total.merge(run)
        per_brief[name] = _summary(run)

    print(f"\n=== TOTALS  ({candidates} candidates, {args.variants} seeds x {len(template_ids) or 20} templates) ===")  # noqa: E501
    _print_row(total, candidates, width=110)
    winners = ", ".join(f"{k}: {v}" for k, v in total.winner_labels.most_common())
    print(f"    winners by candidate: {winners}")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "settings": {
                        "candidates": candidates,
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


def _summary(run: Run) -> dict:
    return {
        "plans": run.plans,
        "candidates_attempted": run.candidates_attempted,
        "unique_topologies": len(run.candidate_labels),
        "winner_labels": dict(run.winner_labels),
        "pruned": run.pruned,
        "pruned_share": run.pruned_share,
        "base_score_avg": mean(run.base_scores) if run.base_scores else None,
        "best_score_avg": mean(run.best_scores) if run.best_scores else None,
        "unique_layouts": len(run.layouts),
        "area_err_mean": mean(run.area_errors) if run.area_errors else None,
        "dim_err": mean(run.dim_errors) if run.dim_errors else None,
        "connectivity": mean(run.connected) if run.connected else None,
        "overlap_rate": run.overlap_rate,
        "feasibility": run.feasibility,
        "time_ms": mean(run.times) * 1000 if run.times else None,
    }


def _print_row(run: Run, candidates: int, width: int = 90) -> None:
    base = mean(run.base_scores) if run.base_scores else float("nan")
    best = mean(run.best_scores) if run.best_scores else float("nan")
    delta = best - base
    conn = mean(run.connected) if run.connected else float("nan")
    feas = run.feasibility if run.feasibility is not None else float("nan")
    area = mean(run.area_errors) if run.area_errors else float("nan")
    dim = mean(run.dim_errors) if run.dim_errors else float("nan")
    t = mean(run.times) if run.times else float("nan")
    print(f"{run.name:<{width}.{width}s}")
    print(
        f"    base-score {base:6.1f}   best-score {best:6.1f}   "
        f"delta {delta:+6.1f}   candidates {run.candidates_attempted / run.plans:5.1f}   "
        f"unique-topologies {len(run.candidate_labels):3d}   "
        f"unique-layouts {len(run.layouts):4d}   "
        f"pruned {run.pruned} ({run.pruned_share:.0%} attempts)"
    )
    print(
        f"    connectivity {conn:5.2%}   feasibility {feas:5.2%}   "
        f"overlap {run.overlap_rate:5.1%}   area-err {area:6.3f}   "
        f"dim-err {dim:6.3f}   time {t*1000:6.0f} ms   ({run.plans} plans)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
