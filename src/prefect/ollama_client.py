from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str
    model: str


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, config: OllamaConfig, *, timeout_seconds: float = 30.0):
        self._config = config
        self._timeout = httpx.Timeout(timeout_seconds)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        context: dict[str, Any] | None = None,
        retries: int = 2,
    ) -> str:
        """Generate a completion using Ollama's HTTP API.

        Logs only metadata (model, prompt lengths), not full content.
        """

        payload: dict[str, Any] = {
            "model": self._config.model,
            "stream": False,
            "system": system_prompt,
            "prompt": user_prompt,
        }
        if context is not None:
            payload["context"] = context

        attempt = 0
        last_error: Exception | None = None

        while attempt <= retries:
            attempt += 1
            try:
                logger.info(
                    "Ollama generate request model=%s system_len=%d user_len=%d attempt=%d",
                    self._config.model,
                    len(system_prompt or ""),
                    len(user_prompt or ""),
                    attempt,
                )

                async with httpx.AsyncClient(base_url=self._config.base_url, timeout=self._timeout) as client:
                    try:
                        resp = await client.post("/api/generate", json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                    except httpx.HTTPStatusError as exc:
                        # Some Ollama setups expose only the OpenAI-compatible API under /v1.
                        if exc.response is not None and exc.response.status_code == 404:
                            data = await self._generate_openai_compat(client, system_prompt, user_prompt)
                        else:
                            raise

                # data can be either native {response: str} or openai-compat {choices:[{message:{content}}]}
                text = data.get("response")
                if not isinstance(text, str):
                    try:
                        choices = data.get("choices")
                        text = choices[0]["message"]["content"] if choices else None
                    except Exception:
                        text = None

                if not isinstance(text, str):
                    raise OllamaError(f"Unexpected Ollama response shape: {json.dumps(data)[:500]}")

                logger.info(
                    "Ollama generate ok model=%s response_len=%d total_duration_ms=%s",
                    self._config.model,
                    len(text),
                    data.get("total_duration"),
                )
                return text

            except (httpx.TimeoutException, httpx.HTTPError, OllamaError) as exc:
                last_error = exc
                logger.warning("Ollama generate failed: %s", exc)
                if attempt <= retries:
                    await asyncio.sleep(0.6 * attempt)

        raise OllamaError(f"Ollama generate failed after {retries + 1} attempts: {last_error}")

    async def _generate_openai_compat(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Fallback to Ollama's OpenAI-compatible API (if enabled).

        Endpoint: /v1/chat/completions
        """

        payload: dict[str, Any] = {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()
