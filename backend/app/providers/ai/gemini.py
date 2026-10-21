"""Google Gemini API provider (REST generateContent with JSON output).

The key is sent in the `x-goog-api-key` header (never in the URL). The model is configurable;
free-tier availability and limits depend on the model and your Google project — check
https://aistudio.google.com/rate-limit. This provider never enables billing or switches models.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.logging import register_secret
from app.providers.ai.base import AIProviderError, AIResult


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 60.0,
        max_input_chars: int = 12_000,
        max_output_tokens: int = 1_024,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise AIProviderError("No Gemini API key configured")
        if not model or "/" in model.removeprefix("models/") or " " in model:
            raise AIProviderError("Invalid Gemini model name")
        register_secret(api_key)
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_input_chars = max_input_chars
        self.max_output_tokens = max_output_tokens
        self.client = client

    def complete_json(self, *, system: str, user: str, purpose: str, inputs: dict[str, Any]) -> AIResult:
        if len(user) > self.max_input_chars:
            user = user[: self.max_input_chars]
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": self.max_output_tokens,
                "temperature": 0.2 if purpose == "classify_reply" else 0.6,
            },
        }
        client = self.client or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderError("Gemini request timed out") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Gemini request failed: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise AIProviderError(f"Gemini rejected the API key ({resp.status_code})")
        if resp.status_code == 429:
            raise AIProviderError("Gemini rate limit or free-tier quota exhausted (429)")
        if resp.status_code == 404:
            raise AIProviderError(f"Gemini model '{self.model}' not found or not available to this key")
        if resp.status_code >= 400:
            raise AIProviderError(f"Gemini error ({resp.status_code})")
        try:
            payload = resp.json()
            cand = payload["candidates"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            block = (payload.get("promptFeedback") or {}).get("blockReason") if isinstance(payload, dict) else None
            raise AIProviderError(f"Gemini returned no candidates{f' (blocked: {block})' if block else ''}") from exc
        finish = cand.get("finishReason")
        if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise AIProviderError(f"Gemini stopped generation ({finish})")
        if finish == "MAX_TOKENS":
            raise AIProviderError("Gemini output hit the token limit; shorten the request or raise GEMINI_MAX_OUTPUT_TOKENS")
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if isinstance(p, dict))
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AIProviderError("Gemini returned non-JSON output") from exc
        if not isinstance(data, dict):
            raise AIProviderError("Gemini returned an unexpected JSON shape")
        usage = payload.get("usageMetadata") or {}
        return AIResult(
            data=data,
            model=payload.get("modelVersion") or self.model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )
