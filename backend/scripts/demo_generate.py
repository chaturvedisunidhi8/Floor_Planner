"""Run the full pipeline once from the command line and write the images.

    python scripts/demo_generate.py --width 30 --length 45 --bhk 3BHK --style modern

Useful for eyeballing output without starting the server or the frontend.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.imaging.pipeline import ImagePipeline
from app.ai.llm.requirement_analyzer import RequirementAnalyzer
from app.ai.retrieval.matcher import TemplateMatcher
from app.ai.retrieval.vector_store import get_vector_store
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.repositories.template_repository import JsonTemplateRepository
from app.schemas.enums import BHKType, Facing, InteriorStyle, PlotShape, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
)
from app.services.generation_service import GenerationService

logger = get_logger("demo")

DEFAULT_ROOMS = [
    RoomType.LIVING_ROOM,
    RoomType.DINING_ROOM,
    RoomType.KITCHEN,
    RoomType.MASTER_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=float, default=30)
    parser.add_argument("--length", type=float, default=45)
    parser.add_argument("--bhk", default="3BHK", choices=[b.value for b in BHKType])
    parser.add_argument("--style", default="modern", choices=[s.value for s in InteriorStyle])
    parser.add_argument("--facing", default="east", choices=[f.value for f in Facing])
    parser.add_argument("--attached-baths", type=int, default=2)
    parser.add_argument("--common-baths", type=int, default=1)
    parser.add_argument("--variants", type=int, default=4)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--features",
        nargs="*",
        default=["balcony", "parking"],
        help="balcony parking garden staircase wash_area",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    requirements = FloorPlanRequirements(
        plot=PlotDetails(
            width_ft=args.width,
            length_ft=args.length,
            shape=PlotShape.SQUARE if args.width == args.length else PlotShape.RECTANGLE,
            facing=Facing(args.facing),
        ),
        bhk=BHKType(args.bhk),
        rooms=DEFAULT_ROOMS,
        bathrooms=BathroomRequirements(
            attached_count=args.attached_baths, common_count=args.common_baths
        ),
        features=[RoomType(f) for f in args.features],
        style=InteriorStyle(args.style),
    )

    repository = JsonTemplateRepository(settings.templates_path)
    service = GenerationService(
        analyzer=RequirementAnalyzer(),
        matcher=TemplateMatcher(repository, get_vector_store()),
        image_pipeline=ImagePipeline(settings),
        settings=settings,
    )

    response = service.generate(requirements, variants=args.variants, seed=args.seed)

    print(f"\nSession    : {response.session_id}")
    print(f"Summary    : {response.requirement_summary}")
    print(f"Analysis by: {response.analysis.get('source')}")

    print("\nTop matches")
    for match in response.matches:
        print(
            f"  {match.rank}. {match.template.id}  {match.score:.3f}  "
            f"{match.template.name}\n      {match.rationale}"
        )

    print("\nGenerated layouts")
    for layout in response.layouts:
        print(
            f"  {layout.name:20s} {layout.render_mode:7s} "
            f"{layout.built_up_sqft:7,.0f} sq ft  {len(layout.rooms):2d} rooms  "
            f"<- {layout.source_template_id}"
        )
        print(f"      {settings.storage_path / layout.image_url.split('/images/')[-1]}")
        for warning in layout.validation_warnings:
            print(f"      ! {warning}")

    if response.warnings:
        print("\nWarnings")
        for warning in response.warnings:
            print(f"  - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
