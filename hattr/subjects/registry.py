from __future__ import annotations

from typing import Any

from .codex import CodexSubject
from .openai_compat import OpenAICompatSubject


def get_subject(cfg: dict[str, Any]):
    adapter = cfg.get("adapter")
    if adapter == "codex":
        return CodexSubject(cfg)
    if adapter == "openai_compat":
        return OpenAICompatSubject(cfg)
    raise ValueError(f"unknown subject adapter: {adapter}")

