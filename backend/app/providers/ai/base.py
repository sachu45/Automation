from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AIResult:
    data: dict[str, Any]
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AIProviderError(Exception):
    pass


class AIProvider(Protocol):
    name: str

    def complete_json(self, *, system: str, user: str, purpose: str, inputs: dict[str, Any]) -> AIResult: ...
