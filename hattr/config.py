from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONDITIONS = ("H1", "H0_ablate", "H0_neutral", "H_base_len", "H_para", "H_contra")


@dataclass(frozen=True)
class StudyConfig:
    path: Path
    name: str
    subject: dict[str, Any]
    output_schema: dict[str, Any] | None
    conditions: dict[str, Any]
    tasks: dict[str, Any]
    scorer: dict[str, Any]
    analysis: dict[str, Any]
    protocol: dict[str, Any]
    raw: dict[str, Any]

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def results_dir(self) -> Path:
        value = self.protocol.get("results_dir", f"hattr_runs/{self.name}")
        path = Path(value)
        return path if path.is_absolute() else Path.cwd() / path

    @property
    def r(self) -> int:
        return int(self.protocol.get("R", 1))

    @property
    def concurrency(self) -> int:
        return int(self.protocol.get("concurrency", 1))

    @property
    def seed(self) -> int:
        return int(self.protocol.get("seed", 12345))

    @property
    def bootstrap_B(self) -> int:
        return int(self.analysis.get("bootstrap_B", 2000))


def load_study(path: str | Path) -> StudyConfig:
    study_path = Path(path).resolve()
    raw = load_raw_study_yaml(study_path)
    _validate(raw, study_path)
    return StudyConfig(
        path=study_path,
        name=str(raw["name"]),
        subject=dict(raw["subject"]),
        output_schema=raw.get("output_schema"),
        conditions=dict(raw["conditions"]),
        tasks=dict(raw["tasks"]),
        scorer=dict(raw["scorer"]),
        analysis=dict(raw["analysis"]),
        protocol=dict(raw.get("protocol", {})),
        raw=raw,
    )


def load_raw_study_yaml(path: str | Path) -> dict[str, Any]:
    study_path = Path(path).resolve()
    text = study_path.read_text(encoding="utf-8")
    text = re.sub(r"^(\s*[^#\n][^:\n]+):\{", r"\1: {", text, flags=re.MULTILINE)
    return yaml.safe_load(text) or {}


def load_jsonl(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def task_path(study: StudyConfig) -> Path:
    value = Path(str(study.tasks["path"]))
    return value if value.is_absolute() else Path.cwd() / value


def _validate(raw: dict[str, Any], path: Path) -> None:
    required = ("name", "subject", "conditions", "tasks", "scorer", "analysis")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"{path}: missing required keys: {', '.join(missing)}")
    variants = raw["conditions"].get("variants", {})
    missing_conditions = [condition for condition in CONDITIONS if condition not in variants]
    if missing_conditions:
        raise ValueError(
            f"{path}: conditions.variants must define: {', '.join(missing_conditions)}"
        )
    if "{variant}" not in str(raw["conditions"].get("base_prompt", "")):
        raise ValueError(f"{path}: conditions.base_prompt must contain {{variant}}")
    for role in ("factual", "ablate", "neutral", "envelope", "paraphrase", "contra"):
        if raw["conditions"].get(role) not in CONDITIONS:
            raise ValueError(f"{path}: conditions.{role} must name one of {CONDITIONS}")
