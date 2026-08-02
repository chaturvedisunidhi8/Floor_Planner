"""Groq chat-completions client.

Thin, retrying wrapper around ``llama-3.3-70b-versatile``. Every call is
optional by design: when no key is configured, :meth:`complete_json` returns
``None`` and callers fall back to deterministic logic.
"""

from __future__ import annotations

import json
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised internally when Groq cannot be reached; never escapes the module."""


class GroqClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return self._settings.llm_enabled

    @property
    def model(self) -> str:
        return self._settings.groq_model

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from groq import Groq

        self._client = Groq(
            api_key=self._settings.groq_api_key,
            timeout=float(self._settings.groq_timeout_seconds),
        )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(LLMUnavailable),
        reraise=True,
    )
    def _call(self, messages: list[dict[str, str]], temperature: float, json_mode: bool) -> str:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2048,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMUnavailable(str(exc)) from exc

        return response.choices[0].message.content or ""

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
    ) -> dict | None:
        """Return the parsed JSON object, or ``None`` if the LLM is unusable."""
        if not self.enabled:
            logger.debug("GROQ_API_KEY not set - skipping LLM call")
            return None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw = self._call(messages, temperature, json_mode=True)
        except LLMUnavailable as exc:
            logger.warning("Groq call failed after retries: %s", exc)
            return None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Groq returned non-JSON content; ignoring it")
            return None

        return parsed if isinstance(parsed, dict) else None

    def complete_text(
        self, system_prompt: str, user_prompt: str, *, temperature: float = 0.5
    ) -> str | None:
        if not self.enabled:
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            return self._call(messages, temperature, json_mode=False).strip()
        except LLMUnavailable as exc:
            logger.warning("Groq call failed after retries: %s", exc)
            return None


_client: GroqClient | None = None


def get_llm_client() -> GroqClient:
    global _client
    if _client is None:
        _client = GroqClient()
    return _client
