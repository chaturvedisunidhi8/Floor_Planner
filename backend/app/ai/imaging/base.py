"""Port for image-generation backends.

Every concrete provider (Hugging Face, Replicate, fal.ai, local diffusers)
implements this interface, so swapping hosts is a configuration change rather
than a code change.
"""

from __future__ import annotations

import base64
import io
from abc import ABC, abstractmethod

from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ImageProviderError


class ImageBackend(ABC):
    """A hosted or local diffusion model."""

    name: str = "abstract"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    @abstractmethod
    def available(self) -> bool:
        """True when the backend has everything it needs to serve a request."""

    @property
    def supports_image_to_image(self) -> bool:
        """Whether the backend can condition on an initial image."""
        return False

    @abstractmethod
    def text_to_image(
        self,
        prompt: str,
        negative_prompt: str,
        *,
        width: int,
        height: int,
        seed: int,
    ) -> Image.Image: ...

    def image_to_image(
        self,
        prompt: str,
        negative_prompt: str,
        init_image: Image.Image,
        *,
        strength: float,
        seed: int,
    ) -> Image.Image:
        raise ImageProviderError(f"{self.name} does not support image-to-image")

    # --- Shared helpers ---------------------------------------------------
    @staticmethod
    def _decode(payload: bytes) -> Image.Image:
        try:
            return Image.open(io.BytesIO(payload)).convert("RGB")
        except Exception as exc:
            raise ImageProviderError(f"Backend returned data that is not an image: {exc}") from exc

    @staticmethod
    def _encode_data_uri(image: Image.Image, fmt: str = "PNG") -> str:
        buffer = io.BytesIO()
        image.save(buffer, format=fmt)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/{fmt.lower()};base64,{encoded}"
