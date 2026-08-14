"""Application configuration.

Every tunable lives here so that no other module reads ``os.environ`` directly.
Settings are loaded once and injected via :func:`get_settings`.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class ImageStrategy(str, Enum):
    """How a layout image is produced."""

    VECTOR = "vector"
    FLUX = "flux"
    HYBRID = "hybrid"


class ImageProvider(str, Enum):
    """Which hosted backend serves FLUX.1-dev."""

    HUGGINGFACE = "huggingface"
    REPLICATE = "replicate"
    FAL = "fal"
    LOCAL = "local"
    NONE = "none"


class GeometryEngine(str, Enum):
    """Which layout engine produces the plans."""

    LEGACY = "legacy"
    SOLVER = "solver"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Floor Planner Agent"
    app_env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Database ----------------------------------------------------------
    database_url: str = ""

    # --- LLM ---------------------------------------------------------------
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_timeout_seconds: int = 45

    # --- Embeddings --------------------------------------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_device: str = "cpu"
    embeddings_enabled: bool = True

    # --- Vector store ------------------------------------------------------
    faiss_index_path: str = "storage/index/templates.faiss"
    faiss_metadata_path: str = "storage/index/templates_meta.json"

    # --- Imaging -----------------------------------------------------------
    image_strategy: ImageStrategy = ImageStrategy.HYBRID
    image_provider: ImageProvider = ImageProvider.HUGGINGFACE
    flux_model: str = "black-forest-labs/FLUX.1-dev"
    huggingface_api_key: str = ""
    replicate_api_token: str = ""
    fal_key: str = ""
    image_timeout_seconds: int = 180
    image_width: int = 1024
    image_height: int = 1024
    flux_img2img_strength: float = 0.35

    # --- Generation --------------------------------------------------------
    top_k_templates: int = Field(default=5, ge=1, le=20)
    variants_per_request: int = Field(default=4, ge=1, le=8)
    storage_dir: str = "storage/images"
    templates_dir: str = "data/templates"
    reference_images_dir: str = "../Templates"

    # --- Geometry engine ----------------------------------------------------
    #: ``legacy`` keeps the classic slicing/repair pipeline (the default for
    #: now); ``solver`` uses the CP-SAT engine, which refuses infeasible briefs
    #: and returns ``status: "infeasible"`` plus diagnostics instead.
    geometry_engine: GeometryEngine = GeometryEngine.LEGACY
    #: Wall-clock budget per CP-SAT solve, in seconds.
    solver_time_limit_seconds: float = Field(default=3.0, ge=0.5, le=30.0)

    # --- Topology search ----------------------------------------------------
    #: How many candidate topologies the solver engine solves per plan before
    #: returning the best-scoring one. ``1`` reproduces the pre-search
    #: behaviour exactly. Each candidate gets its own full time budget, so the
    #: wall-clock cost of a generation scales with this number.
    topology_candidates: int = Field(default=5, ge=1, le=12)
    #: Soft spatial-zoning variants (bedrooms vs social core on opposite sides
    #: of the plot). The main diversity driver.
    topology_zoning: bool = True
    #: Room-order permutation variants. A secondary diversity source.
    topology_permutations: bool = True
    #: Objective credit per room in a zoning variant. Small next to the
    #: area-deviation terms, large enough to steer a typical 1-4 BHK pack.
    topology_bias_weight: int = Field(default=20, ge=1, le=100)

    # --- Derived helpers ---------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """Postgres when configured, otherwise a local SQLite file."""
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(BACKEND_ROOT / 'storage' / 'floorplanner.sqlite3').as_posix()}"

    @property
    def uses_postgres(self) -> bool:
        return self.sqlalchemy_url.startswith("postgres")

    def resolve(self, relative: str) -> Path:
        """Resolve a configured path against the backend root."""
        path = Path(relative)
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()

    @property
    def templates_path(self) -> Path:
        return self.resolve(self.templates_dir)

    @property
    def storage_path(self) -> Path:
        return self.resolve(self.storage_dir)

    @property
    def index_path(self) -> Path:
        return self.resolve(self.faiss_index_path)

    @property
    def index_metadata_path(self) -> Path:
        return self.resolve(self.faiss_metadata_path)

    @property
    def reference_images_path(self) -> Path:
        return self.resolve(self.reference_images_dir)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def image_api_key(self) -> str:
        return {
            ImageProvider.HUGGINGFACE: self.huggingface_api_key,
            ImageProvider.REPLICATE: self.replicate_api_token,
            ImageProvider.FAL: self.fal_key,
        }.get(self.image_provider, "")

    @property
    def flux_enabled(self) -> bool:
        """FLUX only participates when the selected provider is usable."""
        if self.image_strategy is ImageStrategy.VECTOR:
            return False
        if self.image_provider is ImageProvider.NONE:
            return False
        if self.image_provider is ImageProvider.LOCAL:
            return True
        return bool(self.image_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
