"""Probe: which access-run hard values keep feasible briefs feasible?"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.geometry.layout_engine import LayoutEngine
from app.repositories.template_repository import JsonTemplateRepository
from scripts.benchmark import _briefs


def main() -> int:
    run = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    configure_logging("WARNING")
    settings = get_settings()
    repository = JsonTemplateRepository(settings.templates_path)
    template_ids = [f"TPL-{i:03d}" for i in range(1, 13)]
    total_ok = total = 0
    for name, requirements, infeasible in _briefs():
        if infeasible:
            continue
        engine = LayoutEngine(requirements)
        failed = []
        for template_id in template_ids:
            template = repository.get(template_id)
            plan = engine.generate_solver(
                template,
                seed=100,
                variation_index=0,
                time_limit=1.0,
                topology_candidates=3,
                access_run_ft=run,
            )
            total += 1
            if plan.status == "feasible":
                total_ok += 1
            else:
                failed.append(template_id)
        print(f"{name:<28} {total_ok}-so-far -> {len(template_ids)-len(failed)}/{len(template_ids)}"
              + (f"  FAILED {', '.join(failed)}" if failed else ""))
    print(f"\nTOTAL {total_ok}/{total} feasible at run={run}")
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
