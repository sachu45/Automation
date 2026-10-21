"""Any OpenAI-compatible /chat/completions endpoint (OpenAI, Azure-compatible gateways, local servers)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.logging import register_secret
from app.providers.ai.base import AIProviderError, AIResult


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0, client: httpx.Client | None = None):
        if not api_key:
            raise AIProviderError("No AI API key configured")
        register_secret(api_key)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.client = client

    def complete_json(self, *, system: str, user: str, purpose: str, inputs: dict[str, Any]) -> AIResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2 if purpose == "classify_reply" else 0.7,
        }
        client = self.client or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise AIProviderError(f"AI request failed: {type(exc).__name__}") from exc
        if resp.status_code == 401:
            raise AIProviderError("AI provider rejected the API key (401)")
        if resp.status_code == 429:
            raise AIProviderError("AI provider rate limit or quota exceeded (429)")
        if resp.status_code >= 400:
            raise AIProviderError(f"AI provider error ({resp.status_code})")
        try:
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise AIProviderError("AI provider returned an unexpected or non-JSON response") from exc
        usage = payload.get("usage") or {}
        return AIResult(
            data=data,
            model=payload.get("model") or self.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
