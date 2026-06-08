from __future__ import annotations

import sys
from typing import Any


def static_warnings(study: Any) -> list[str]:
    warnings: list[str] = []
    scorer = study.scorer
    if scorer.get("type") == "llm_judge":
        subject = study.subject
        judge = scorer.get("judge", {})
        if _adapter_model(subject) == _adapter_model(judge):
            warnings.append(
                "judge==subject: scorer-bias risk because the LLM judge matches the subject."
            )
        if not study.analysis.get("secondary_events"):
            warnings.append(
                "llm_judge secondary_events missing: tradeoffs may be missed."
            )
    if not study.conditions.get("envelope"):
        warnings.append("envelope role missing: length effects cannot be separated.")
    return warnings


def runtime_warnings(analysis: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if analysis.get("screen"):
        triage = analysis.get("triage", {})
        rate_values = (
            triage.get("e_factual"),
            triage.get("e_ablate"),
            triage.get("e_envelope"),
        )
    else:
        verdict = analysis.get("verdict", {})
        rate_values = tuple(verdict.get("bad_primary_rates", {}).values())
    rates = [value for value in rate_values if value is not None]
    if rates and max(rates) - min(rates) <= 0.01:
        warnings.append(
            "headroom: primary event rates are nearly identical across conditions."
        )
    rows = int(analysis.get("rows", 0) or 0)
    errors = int(analysis.get("errors", 0) or 0)
    rate = (errors / rows) if rows else 0.0
    warnings.append(f"error_rate: {errors} / {rows} ({rate:.1%}).")
    return warnings


def emit(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"hattr guardrail: {warning}", file=sys.stderr)


def _adapter_model(cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    adapter = cfg.get("adapter")
    model = cfg.get("model")
    if adapter == "codex" and model is None:
        model = "gpt-5.5"
    return (str(adapter) if adapter is not None else None, str(model) if model else None)
