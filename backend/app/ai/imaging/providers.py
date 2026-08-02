"""Concrete FLUX.1-dev backends.

Hugging Face is the configured default; the others are wired up so switching
host is a one-line change in ``.env``.
"""

from __future__ import annotations

import time

import httpx
from PIL import Image

from app.ai.imaging.base import ImageBackend
from app.core.config import ImageProvider, Settings
from app.core.exceptions import ImageProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)


class HuggingFaceBackend(ImageBackend):
    """FLUX.1-dev through the Hugging Face Inference API.

    The serverless endpoint is text-to-image only and cold-starts the model on
    first use, answering 503 with an ``estimated_time`` until it is warm.
    """

    name = "huggingface"
    BASE_URL = "https://api-inference.huggingface.co/models"
    MAX_COLD_START_RETRIES = 3

    @property
    def available(self) -> bool:
        return bool(self._settings.huggingface_api_key)

    def text_to_image(
        self, prompt: str, negative_prompt: str, *, width: int, height: int, seed: int
    ) -> Image.Image:
        if not self.available:
            raise ImageProviderError("HUGGINGFACE_API_KEY is not configured")

        url = f"{self.BASE_URL}/{self._settings.flux_model}"
        payload = {
            "inputs": prompt,
            "parameters": {
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "seed": seed,
            },
            "options": {"wait_for_model": True, "use_cache": False},
        }
        headers = {
            "Authorization": f"Bearer {self._settings.huggingface_api_key}",
            "Accept": "image/png",
        }

        timeout = float(self._settings.image_timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            for attempt in range(1, self.MAX_COLD_START_RETRIES + 1):
                response = client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    return self._decode(response.content)

                if response.status_code == 503 and attempt < self.MAX_COLD_START_RETRIES:
                    wait = self._cold_start_delay(response)
                    logger.info(
                        "FLUX is loading on Hugging Face; retrying in %.0fs (attempt %d/%d)",
                        wait,
                        attempt,
                        self.MAX_COLD_START_RETRIES,
                    )
                    time.sleep(wait)
                    continue

                raise ImageProviderError(
                    f"Hugging Face returned {response.status_code}: {response.text[:200]}"
                )

        raise ImageProviderError("FLUX did not become ready on Hugging Face in time")

    @staticmethod
    def _cold_start_delay(response: httpx.Response) -> float:
        try:
            estimated = float(response.json().get("estimated_time", 20.0))
        except Exception:
            estimated = 20.0
        return min(max(estimated, 5.0), 60.0)


class ReplicateBackend(ImageBackend):
    """FLUX.1-dev on Replicate. Supports image conditioning."""

    name = "replicate"
    BASE_URL = "https://api.replicate.com/v1/models"
    POLL_INTERVAL = 2.0

    @property
    def available(self) -> bool:
        return bool(self._settings.replicate_api_token)

    @property
    def supports_image_to_image(self) -> bool:
        return True

    def text_to_image(
        self, prompt: str, negative_prompt: str, *, width: int, height: int, seed: int
    ) -> Image.Image:
        return self._predict(
            {
                "prompt": prompt,
                "aspect_ratio": "1:1" if width == height else "custom",
                "width": width,
                "height": height,
                "num_inference_steps": 28,
                "guidance": 3.5,
                "seed": seed,
                "output_format": "png",
            }
        )

    def image_to_image(
        self,
        prompt: str,
        negative_prompt: str,
        init_image: Image.Image,
        *,
        strength: float,
        seed: int,
    ) -> Image.Image:
        return self._predict(
            {
                "prompt": prompt,
                "image": self._encode_data_uri(init_image),
                "prompt_strength": strength,
                "num_inference_steps": 28,
                "guidance": 3.5,
                "seed": seed,
                "output_format": "png",
            }
        )

    def _predict(self, payload: dict) -> Image.Image:
        if not self.available:
            raise ImageProviderError("REPLICATE_API_TOKEN is not configured")

        headers = {
            "Authorization": f"Bearer {self._settings.replicate_api_token}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }
        url = f"{self.BASE_URL}/{self._settings.flux_model}/predictions"
        deadline = time.monotonic() + self._settings.image_timeout_seconds

        with httpx.Client(timeout=float(self._settings.image_timeout_seconds)) as client:
            response = client.post(url, json={"input": payload}, headers=headers)
            if response.status_code not in (200, 201):
                raise ImageProviderError(
                    f"Replicate returned {response.status_code}: {response.text[:200]}"
                )

            prediction = response.json()
            while prediction.get("status") in ("starting", "processing"):
                if time.monotonic() > deadline:
                    raise ImageProviderError("Replicate prediction timed out")
                time.sleep(self.POLL_INTERVAL)
                prediction = client.get(prediction["urls"]["get"], headers=headers).json()

            if prediction.get("status") != "succeeded":
                raise ImageProviderError(
                    f"Replicate prediction {prediction.get('status')}: "
                    f"{str(prediction.get('error'))[:200]}"
                )

            output = prediction.get("output")
            image_url = output[0] if isinstance(output, list) else output
            if not isinstance(image_url, str):
                raise ImageProviderError("Replicate returned no image URL")
            return self._decode(client.get(image_url).content)


class FalBackend(ImageBackend):
    """FLUX.1-dev on fal.ai. Fastest option and supports image conditioning."""

    name = "fal"
    TEXT_URL = "https://fal.run/fal-ai/flux/dev"
    IMAGE_URL = "https://fal.run/fal-ai/flux/dev/image-to-image"

    @property
    def available(self) -> bool:
        return bool(self._settings.fal_key)

    @property
    def supports_image_to_image(self) -> bool:
        return True

    def text_to_image(
        self, prompt: str, negative_prompt: str, *, width: int, height: int, seed: int
    ) -> Image.Image:
        return self._run(
            self.TEXT_URL,
            {
                "prompt": prompt,
                "image_size": {"width": width, "height": height},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "seed": seed,
                "num_images": 1,
                "enable_safety_checker": True,
            },
        )

    def image_to_image(
        self,
        prompt: str,
        negative_prompt: str,
        init_image: Image.Image,
        *,
        strength: float,
        seed: int,
    ) -> Image.Image:
        return self._run(
            self.IMAGE_URL,
            {
                "prompt": prompt,
                "image_url": self._encode_data_uri(init_image),
                "strength": strength,
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "seed": seed,
                "num_images": 1,
            },
        )

    def _run(self, url: str, payload: dict) -> Image.Image:
        if not self.available:
            raise ImageProviderError("FAL_KEY is not configured")

        headers = {
            "Authorization": f"Key {self._settings.fal_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=float(self._settings.image_timeout_seconds)) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise ImageProviderError(
                    f"fal.ai returned {response.status_code}: {response.text[:200]}"
                )
            images = response.json().get("images") or []
            if not images:
                raise ImageProviderError("fal.ai returned no images")
            return self._decode(client.get(images[0]["url"]).content)


class LocalDiffusersBackend(ImageBackend):
    """FLUX.1-dev running locally through ``diffusers``.

    Needs a CUDA device with roughly 24 GB of VRAM. The pipeline is loaded once
    and kept resident, so the first request pays the whole model load.
    """

    name = "local"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._pipe = None

    @property
    def available(self) -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except ImportError:
            return False

    @property
    def supports_image_to_image(self) -> bool:
        return True

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        import torch
        from diffusers import FluxPipeline

        logger.info("Loading %s locally - this takes a while", self._settings.flux_model)
        self._pipe = FluxPipeline.from_pretrained(
            self._settings.flux_model, torch_dtype=torch.bfloat16
        ).to("cuda")
        return self._pipe

    def text_to_image(
        self, prompt: str, negative_prompt: str, *, width: int, height: int, seed: int
    ) -> Image.Image:
        import torch

        pipe = self._load()
        generator = torch.Generator("cuda").manual_seed(seed)
        result = pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=28,
            guidance_scale=3.5,
            generator=generator,
        )
        return result.images[0].convert("RGB")

    def image_to_image(
        self,
        prompt: str,
        negative_prompt: str,
        init_image: Image.Image,
        *,
        strength: float,
        seed: int,
    ) -> Image.Image:
        import torch
        from diffusers import FluxImg2ImgPipeline

        base = self._load()
        pipe = FluxImg2ImgPipeline(**base.components).to("cuda")
        generator = torch.Generator("cuda").manual_seed(seed)
        result = pipe(
            prompt=prompt,
            image=init_image,
            strength=strength,
            num_inference_steps=28,
            guidance_scale=3.5,
            generator=generator,
        )
        return result.images[0].convert("RGB")


class NullBackend(ImageBackend):
    """No remote model. The vector renderer is the whole pipeline."""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    def text_to_image(
        self, prompt: str, negative_prompt: str, *, width: int, height: int, seed: int
    ) -> Image.Image:
        raise ImageProviderError("No image provider is configured")


BACKENDS: dict[ImageProvider, type[ImageBackend]] = {
    ImageProvider.HUGGINGFACE: HuggingFaceBackend,
    ImageProvider.REPLICATE: ReplicateBackend,
    ImageProvider.FAL: FalBackend,
    ImageProvider.LOCAL: LocalDiffusersBackend,
    ImageProvider.NONE: NullBackend,
}


def build_backend(settings: Settings) -> ImageBackend:
    backend_cls = BACKENDS.get(settings.image_provider, NullBackend)
    return backend_cls(settings)
