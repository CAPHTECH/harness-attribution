from __future__ import annotations

from typing import Any

from .execution import ExecutionScorer
from .llm_judge import LLMJudgeScorer
from .regex import RegexScorer


def get_scorer(cfg: dict[str, Any]):
    scorer_type = cfg.get("type")
    if scorer_type == "execution":
        return ExecutionScorer(cfg)
    if scorer_type == "llm_judge":
        return LLMJudgeScorer(cfg)
    if scorer_type == "regex":
        return RegexScorer(cfg)
    raise ValueError(f"unknown scorer type: {scorer_type}")

