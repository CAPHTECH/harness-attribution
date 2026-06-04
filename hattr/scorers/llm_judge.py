from __future__ import annotations

import json
import re
from typing import Any

from hattr.subjects.registry import get_subject


class LLMJudgeScorer:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.judge = get_subject(dict(cfg["judge"]))
        self.rubric = str(cfg.get("rubric", ""))
        self.output_schema = cfg.get("output_schema")
        self.events_cfg = dict(cfg.get("events", {}))
        self.event_names = list(self.events_cfg)
        self.crosscheck = cfg.get("deterministic_crosscheck") or None

    def score(
        self,
        task: dict[str, Any],
        output: str | dict[str, Any],
        subject_error: bool,
        mock: bool = False,
    ) -> dict[str, Any]:
        if subject_error:
            return {name: None for name in self.event_names}
        answer = _answer_text(output)
        subset = str(task.get("subset", task.get("_subset", "all")))
        if mock:
            judgment = _mock_judgment(subset, answer)
            judge_error = False
            judge_duration = 0.0
        else:
            prompt = self._build_prompt(task, subset, answer)
            gen = self.judge.generate(prompt, self.output_schema)
            judge_error = bool(gen.get("error"))
            judge_duration = float(gen.get("duration_s", 0.0) or 0.0)
            judgment = {}
            if not judge_error and isinstance(gen.get("output"), dict):
                judgment = dict(gen["output"])
            elif not judge_error:
                try:
                    judgment = json.loads(str(gen.get("output", "")))
                except json.JSONDecodeError:
                    judge_error = True
        if judge_error:
            row = {name: None for name in self.event_names}
            row.update({"scorer_error": True, "judge_duration_s": judge_duration})
            return row
        row = self._events_from_judgment(judgment, subset)
        row.update(judgment)
        row["scorer_error"] = False
        row["judge_duration_s"] = judge_duration
        if self.crosscheck:
            field = str(self.crosscheck["field"])
            regex = re.compile(str(self.crosscheck["regex"]))
            judged = bool(judgment.get(field))
            ruled = bool(regex.search(answer))
            row[f"{field}_deterministic"] = ruled
            row[f"{field}_agreement"] = judged == ruled
        return row

    def _build_prompt(self, task: dict[str, Any], subset: str, answer: str) -> str:
        payload = {
            "question": task.get("question"),
            "subset": subset,
            "reference": task.get("reference"),
            "answer": answer,
        }
        return (
            f"{self.rubric}\n\n"
            "JSON スキーマに厳密に従って返してください。\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _events_from_judgment(self, judgment: dict[str, Any], subset: str) -> dict[str, Any]:
        namespace = dict(judgment)
        events: dict[str, Any] = {}
        for name, cfg in self.events_cfg.items():
            event_subset = cfg.get("subset")
            if event_subset is not None and str(event_subset) != subset:
                events[name] = None
                continue
            try:
                events[name] = bool(
                    eval(str(cfg["expr"]), {"__builtins__": {}}, namespace)
                )
            except Exception:
                events[name] = None
        return events


def _answer_text(output: str | dict[str, Any]) -> str:
    if isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False, sort_keys=True)
    return str(output)


def _mock_judgment(subset: str, answer: str) -> dict[str, Any]:
    is_abstention = any(
        token in answer for token in ("わかりません", "分かりません", "不明", "特定できない")
    )
    return {
        "is_abstention": is_abstention,
        "is_confident_specific_claim": False,
        "is_refusal": False,
        "answer_matches_reference": "true" if subset == "answerable" else "na",
        "rationale": "mock",
    }

