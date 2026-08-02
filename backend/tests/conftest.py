"""Shared fixtures.

Everything runs against a temporary SQLite database, a temporary image
directory and the JSON knowledge base, so the suite needs no credentials and
no external services.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Must be set before app.core.config is first imported.
os.environ.setdefault("EMBEDDINGS_ENABLED", "false")
os.environ.setdefault("IMAGE_STRATEGY", "vector")
os.environ.setdefault("IMAGE_PROVIDER", "none")
os.environ.setdefault("GROQ_API_KEY", "")

from app.ai.embeddings.encoder import reset_encoder  # noqa: E402
from app.ai.imaging.pipeline import reset_image_pipeline  # noqa: E402
from app.ai.retrieval.vector_store import reset_vector_store  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.repositories.template_repository import JsonTemplateRepository  # noqa: E402
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType  # noqa: E402
from app.schemas.requirements import (  # noqa: E402
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
)


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point storage and the index at a throwaway directory for the session."""
    workspace = tmp_path_factory.mktemp("floorplanner")
    settings = get_settings()
    settings.storage_dir = str(workspace / "images")
    settings.faiss_index_path = str(workspace / "index" / "templates.faiss")
    settings.faiss_metadata_path = str(workspace / "index" / "templates_meta.json")
    settings.database_url = f"sqlite:///{(workspace / 'test.sqlite3').as_posix()}"

    settings.storage_path.mkdir(parents=True, exist_ok=True)
    settings.index_path.parent.mkdir(parents=True, exist_ok=True)

    reset_encoder()
    reset_vector_store()
    reset_image_pipeline()
    yield


@pytest.fixture(scope="session")
def templates():
    return JsonTemplateRepository(get_settings().templates_path).list_all()


@pytest.fixture(scope="session")
def repository():
    return JsonTemplateRepository(get_settings().templates_path)


@pytest.fixture
def requirements() -> FloorPlanRequirements:
    """A typical 3BHK brief - the shape most tests exercise."""
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=30, length_ft=45, facing=Facing.EAST),
        bhk=BHKType.BHK3,
        rooms=[
            RoomType.LIVING_ROOM,
            RoomType.DINING_ROOM,
            RoomType.KITCHEN,
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
        ],
        bathrooms=BathroomRequirements(attached_count=2, common_count=1),
        features=[RoomType.BALCONY, RoomType.PARKING],
        style=InteriorStyle.MODERN,
    )


def make_requirements(**overrides) -> FloorPlanRequirements:
    """Build a brief with selected fields replaced."""
    base = {
        "plot": PlotDetails(width_ft=30, length_ft=45),
        "bhk": BHKType.BHK3,
        "rooms": [RoomType.LIVING_ROOM, RoomType.KITCHEN],
        "bathrooms": BathroomRequirements(attached_count=1, common_count=1),
        "features": [],
        "style": InteriorStyle.MODERN,
    }
    base.update(overrides)
    return FloorPlanRequirements(**base)
