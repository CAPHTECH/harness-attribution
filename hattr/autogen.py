from __future__ import annotations

import string
from pathlib import Path
from typing import Any

import yaml

from .config import load_raw_study_yaml
from .subjects.registry import get_subject


AUTOGEN_NOTE = (
    "自動生成。preregister 前にレビューせよ"
    "（post-hoc/変動の注意）。"
)


def autogen_variants(
    study_path: str | Path,
    timestamp: str,
    generator_model: str | None = None,
) -> dict[str, Any]:
    path = Path(study_path).resolve()
    raw = load_raw_study_yaml(path)
    conditions = raw.get("conditions", {})
    variants = conditions.setdefault("variants", {})
    factual_key = conditions.get("factual")
    factual = variants.get(factual_key)
    if not isinstance(factual, str):
        message = (
            f"skip: factual variant {factual_key!r} is not a single string; "
            "autogen targets only single-string variants"
        )
        print(message)
        return {"changed": False, "skipped": True, "message": message}
    if _has_format_fields(factual):
        message = (
            f"skip: factual variant {factual_key!r} contains per-task fields; "
            "autogen targets safety-style single-string variants"
        )
        print(message)
        return {"changed": False, "skipped": True, "message": message}

    targets = _missing_targets(conditions, variants)
    if not targets:
        message = "no changes: paraphrase/envelope variants are already set"
        print(message)
        return {"changed": False, "skipped": False, "message": message}

    subject_cfg = dict(raw["subject"])
    if generator_model is not None:
        subject_cfg["model"] = generator_model
    generator = get_subject(subject_cfg)
    schema = _schema(targets)
    gen = generator.generate(_prompt(factual, targets), schema)
    if gen.get("error"):
        raise RuntimeError(f"autogen generator failed: {gen.get('stderr', '')}")
    output = gen.get("output")
    if not isinstance(output, dict):
        raise ValueError("autogen generator did not return a JSON object")

    generated: dict[str, str] = {}
    suffix = _trailing_whitespace(factual)
    for role, key in targets.items():
        value = output.get(role)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"autogen generator returned an empty {role!r} string")
        generated_value = value.strip() + suffix
        variants[key] = generated_value
        generated[key] = generated_value

    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"timestamp: {timestamp}")
    for key, value in generated.items():
        print(f"{key}: {value!r}")
    print(AUTOGEN_NOTE)
    return {"changed": True, "skipped": False, "generated": generated}


def _missing_targets(
    conditions: dict[str, Any], variants: dict[str, Any]
) -> dict[str, str]:
    targets: dict[str, str] = {}
    for role in ("paraphrase", "envelope"):
        key = conditions.get(role)
        if not isinstance(key, str):
            raise ValueError(f"conditions.{role} must name a variant key")
        value = variants.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            targets[role] = key
    return targets


def _schema(targets: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {role: {"type": "string"} for role in targets},
        "required": list(targets),
    }


def _prompt(factual: str, targets: dict[str, str]) -> str:
    requested = ", ".join(targets)
    return f"""\
You generate frozen condition variants for a harness attribution study.
Return JSON with exactly these fields: {requested}.

factual variant:
{factual.rstrip()}

Rules:
- paraphrase: rewrite the factual instruction while preserving its meaning.
- envelope: write neutral filler unrelated to the target behavior, with roughly the same length as the factual variant.
- Keep the same language as the factual variant.
- Do not mention this study, preregistration, generation, or dates.
- Return only the requested JSON object.
"""


def _has_format_fields(value: str) -> bool:
    formatter = string.Formatter()
    return any(field_name is not None for _, field_name, _, _ in formatter.parse(value))


def _trailing_whitespace(value: str) -> str:
    return value[len(value.rstrip()) :]
