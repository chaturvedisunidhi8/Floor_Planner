"""Render winning solver plans to PNG for visual inspection.

    python scripts/render_winner.py                     # defaults below
    python scripts/render_winner.py --brief 3BHK --templates TPL-001 TPL-007

Each rendered image is the *winning* plan of the adaptive search for that
(brief, template, seed): what the engine actually returns, including the
modeled doors, windows and wall thicknesses. Images land in
``benchmarks/renders/`` as ``<brief>_<template>_seed<seed>.png``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.geometry.layout_engine import LayoutEngine
from app.geometry.renderer import render_to_file
from app.repositories.template_repository import JsonTemplateRepository
from app.schemas.enums import InteriorStyle
from scripts.sanity_check import _brief


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", nargs="+", default=["TPL-001", "TPL-007"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[100, 200])
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--time-limit", type=float, default=1.0)
    parser.add_argument(
        "--out",
        type=str,
        default="benchmarks/renders",
        help="output directory (relative to the repo root)",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    requirements = _brief()
    repository = JsonTemplateRepository(settings.templates_path)
    engine = LayoutEngine(requirements)
    out_dir = Path(__file__).resolve().parents[2] / args.out

    rendered = 0
    for template_id in args.templates:
        template = repository.get(template_id)
        for seed in args.seeds:
            plan = engine.generate_solver(
                template,
                seed=seed,
                variation_index=0,
                time_limit=args.time_limit,
                topology_candidates=args.candidates,
            )
            if plan.status != "feasible":
                print(f"  {template.id} seed={seed}: {plan.status} - skipped")
                continue
            destination = out_dir / f"3bhk_{template.id}_seed{seed}.png"
            survivors = (
                e
                for e in (plan.topology_search or [])
                if e["status"] == "feasible" and e.get("score") is not None
            )
            winner = max(survivors, key=lambda e: e["score"], default={}).get("label", "?")
            render_to_file(
                plan,
                destination,
                style=InteriorStyle.MODERN,
                title=f"3BHK standard 30x45 - {template.name}",
                subtitle=f"score {plan.quality_score:.1f}  seed {seed}  winner {winner}",
            )
            print(f"  {destination}  score={plan.quality_score:.1f}")
            rendered += 1

    print(f"\nRendered {rendered} plan(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
