from __future__ import annotations

import hashlib
from typing import Any

from .config import CONDITIONS


def build_prompt(task: dict[str, Any], conditions: dict[str, Any], condition: str) -> str:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    variants = conditions.get("variants", {})
    variant = str(variants.get(condition, ""))
    base = str(conditions["base_prompt"]).replace("{variant}", variant)
    return base.format(**task)


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

