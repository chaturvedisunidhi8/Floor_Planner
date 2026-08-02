"""Image generation pipeline.

Three strategies, selected by ``IMAGE_STRATEGY``:

``vector``
    The geometry renderer alone. Always correct, always available, no keys.

``flux``
    FLUX.1-dev text-to-image from the built prompt. Matches the specification
    literally; room labels will be diffusion-model gibberish.

``hybrid`` (default)
    The geometry engine draws the plan, FLUX supplies the material and lighting
    treatment, and the drawing's linework - walls, door swings, dimensions and
    room labels - is composited back on top at full opacity. The result reads
    as a rendered architectural plan while every label and dimension remains
    exactly what the engine computed.

If FLUX fails for any reason the pipeline falls back to the vector render and
records a warning; a generation request never fails because of the image host.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from app.ai.imaging.base import ImageBackend
from app.ai.imaging.providers import build_backend
from app.ai.prompting.prompt_builder import ImagePrompt, PromptBuilder, get_prompt_builder
from app.core.config import ImageStrategy, Settings, get_settings
from app.core.exceptions import ImageProviderError
from app.core.logging import get_logger
from app.geometry.layout_engine import LayoutPlan
from app.geometry.renderer import FloorPlanRenderer
from app.schemas.enums import InteriorStyle
from app.schemas.requirements import FloorPlanRequirements

logger = get_logger(__name__)

#: How strongly the FLUX texture shows through the flat fills in hybrid mode
#: when the backend is text-to-image only. Low enough that the plan's own
#: colour scheme still dominates.
TEXTURE_OPACITY = 0.34


@dataclass
class RenderedImage:
    path: Path
    mode: str
    warning: str | None = None


class ImagePipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        backend: ImageBackend | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._backend = backend or build_backend(self._settings)
        self._prompts = prompt_builder or get_prompt_builder()
        self._renderer = FloorPlanRenderer(
            self._settings.image_width, self._settings.image_height
        )

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def flux_active(self) -> bool:
        """Whether a diffusion model will actually be called.

        The backend is the authority on its own readiness - that keeps an
        injected backend (tests, a custom host) from being vetoed by the
        credential check for whichever provider happens to be configured.
        """
        return (
            self._settings.image_strategy is not ImageStrategy.VECTOR
            and self._backend.available
        )

    def generate(
        self,
        plan: LayoutPlan,
        requirements: FloorPlanRequirements,
        *,
        destination: Path,
        title: str,
        subtitle: str,
    ) -> RenderedImage:
        prompt = self._prompts.build(plan, requirements, variation_label=plan.variation)
        strategy = self._settings.image_strategy

        if strategy is not ImageStrategy.VECTOR and not self.flux_active:
            logger.debug("FLUX unavailable; rendering '%s' with the vector engine", title)
            strategy = ImageStrategy.VECTOR

        try:
            if strategy is ImageStrategy.VECTOR:
                image = self._vector(plan, requirements.style, title, subtitle)
                mode, warning = "vector", None
            elif strategy is ImageStrategy.FLUX:
                image, mode, warning = self._flux_only(prompt), "flux", None
            else:
                image, mode, warning = self._hybrid(plan, requirements, title, subtitle, prompt)
        except ImageProviderError as exc:
            logger.warning("Image provider failed (%s); falling back to the vector render", exc)
            image = self._vector(plan, requirements.style, title, subtitle)
            mode, warning = "vector", f"FLUX unavailable, drew the plan directly ({exc})"
        except Exception as exc:
            logger.exception("Unexpected image failure; falling back to the vector render")
            image = self._vector(plan, requirements.style, title, subtitle)
            mode, warning = "vector", f"Image stylisation failed ({type(exc).__name__})"

        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG", optimize=True)
        return RenderedImage(path=destination, mode=mode, warning=warning)

    # --- Strategies -------------------------------------------------------
    def _vector(
        self, plan: LayoutPlan, style: InteriorStyle, title: str, subtitle: str
    ) -> Image.Image:
        return self._renderer.render(plan, style=style, title=title, subtitle=subtitle)

    def _flux_only(self, prompt: ImagePrompt) -> Image.Image:
        return self._backend.text_to_image(
            prompt.positive,
            prompt.negative,
            width=self._settings.image_width,
            height=self._settings.image_height,
            seed=prompt.seed,
        )

    def _hybrid(
        self,
        plan: LayoutPlan,
        requirements: FloorPlanRequirements,
        title: str,
        subtitle: str,
        prompt: ImagePrompt,
    ) -> tuple[Image.Image, str, str | None]:
        fills, linework = self._renderer.render_layers(
            plan, style=requirements.style, title=title, subtitle=subtitle
        )

        if self._backend.supports_image_to_image:
            # The backend can see the drawing, so let it repaint the surfaces
            # at low strength - geometry survives, materials improve.
            stylised = self._backend.image_to_image(
                prompt.positive,
                prompt.negative,
                fills,
                strength=self._settings.flux_img2img_strength,
                seed=prompt.seed,
            )
            base = self._blend(fills, stylised, weight=0.85)
        else:
            # Text-to-image only: FLUX cannot see the plan, so its output is
            # used purely as a texture wash under the drawing.
            texture = self._flux_only(prompt)
            base = self._blend(fills, texture, weight=TEXTURE_OPACITY)

        composed = base.convert("RGBA")
        composed.alpha_composite(linework.convert("RGBA"))
        return composed.convert("RGB"), "hybrid", None

    # --- Compositing ------------------------------------------------------
    def _blend(self, base: Image.Image, overlay: Image.Image, *, weight: float) -> Image.Image:
        """Blend a generated image over the flat fills without muddying them."""
        if overlay.size != base.size:
            overlay = overlay.resize(base.size, Image.LANCZOS)

        # Softening kills the model's invented micro-text and stray linework,
        # leaving the tonal variation that is actually wanted.
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=1.1))
        overlay = ImageEnhance.Color(overlay).enhance(0.72)

        blended = Image.blend(base.convert("RGB"), overlay.convert("RGB"), weight)
        return ImageEnhance.Brightness(blended).enhance(1.04)


_pipeline: ImagePipeline | None = None


def get_image_pipeline() -> ImagePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ImagePipeline()
    return _pipeline


def reset_image_pipeline() -> None:
    """Test hook."""
    global _pipeline
    _pipeline = None
