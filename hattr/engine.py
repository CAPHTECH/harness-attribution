from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .analysis import analyze_rows
from .conditions import build_prompt, prompt_hash
from .config import CONDITIONS, StudyConfig, load_jsonl, task_path
from .guardrails import emit, runtime_warnings, static_warnings
from .prereg import drift_warning
from .report import write_outputs
from .scorers.registry import get_scorer
from .subjects.registry import get_subject


def run_study(
    study: StudyConfig,
    limit: int | None = None,
    mock: bool = False,
    screen: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    results_dir = study.results_dir
    raw_dir = results_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    warnings = static_warnings(study)
    drift = drift_warning(study.path, study.raw)
    if drift:
        warnings.append(drift)
    emit(warnings)

    tasks = load_jsonl(task_path(study), limit=limit)
    cluster_id_field = str(study.tasks.get("cluster_id_field", "id"))
    subset_field = study.tasks.get("subset_field")
    subject = None if mock else get_subject(study.subject)
    scorer = get_scorer(study.scorer)
    conditions = _screen_conditions(study) if screen else CONDITIONS
    r = 1 if screen else study.r

    jobs = [
        (task, condition, sample_id)
        for task in tasks
        for condition in conditions
        for sample_id in range(1, r + 1)
    ]
    rows: list[dict[str, Any]] = []
    max_workers = max(1, study.concurrency)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {
            executor.submit(
                _run_one,
                study,
                scorer,
                subject,
                raw_dir,
                task,
                cluster_id_field,
                subset_field,
                condition,
                sample_id,
                mock,
            ): (task, condition, sample_id)
            for task, condition, sample_id in jobs
        }
        for future in as_completed(future_to_job):
            rows.append(future.result())

    rows.sort(key=lambda row: (str(row["question_id"]), str(row["condition"]), int(row["sample_id"])))
    event_names = list(getattr(scorer, "event_names", []))
    analysis_cfg = dict(study.analysis)
    analysis_cfg["bootstrap_B"] = 0 if screen else study.bootstrap_B
    analysis = analyze_rows(
        rows,
        analysis_cfg,
        event_names,
        study.seed,
        conditions=conditions,
        condition_roles=study.conditions,
        screen=screen,
    )
    warnings = [*warnings, *runtime_warnings(analysis)]
    emit(runtime_warnings(analysis))

    meta = {
        "study": study.name,
        "study_path": str(study.path),
        "mock": mock,
        "screen": screen,
        "limit": limit,
        "R": r,
        "tasks": len(tasks),
        "total_rows": len(rows),
        "duration_s": time.perf_counter() - started,
        "results_dir": str(results_dir),
    }
    if screen:
        meta["triage"] = analysis["triage"]["label"]
        meta["conditions"] = list(conditions)
    else:
        meta["verdict"] = analysis["verdict"]["verdict"]
    write_outputs(results_dir, rows, analysis, warnings, meta)
    return {"rows": rows, "analysis": analysis, "warnings": warnings, "meta": meta}


def _screen_conditions(study: StudyConfig) -> tuple[str, str, str]:
    missing = [
        role
        for role in ("factual", "ablate", "envelope")
        if study.conditions.get(role) not in CONDITIONS
    ]
    if missing:
        raise ValueError(
            "screen mode requires conditions roles: "
            f"{', '.join(missing)} must name one of {CONDITIONS}"
        )
    factual = str(study.conditions["factual"])
    ablate = str(study.conditions["ablate"])
    envelope = str(study.conditions["envelope"])
    return (factual, ablate, envelope)


def _run_one(
    study: StudyConfig,
    scorer: Any,
    subject: Any,
    raw_dir: Path,
    task: dict[str, Any],
    cluster_id_field: str,
    subset_field: str | None,
    condition: str,
    sample_id: int,
    mock: bool,
) -> dict[str, Any]:
    task = dict(task)
    subset = str(task.get(subset_field, "all")) if subset_field else "all"
    task["_subset"] = subset
    question_id = str(task[cluster_id_field])
    prompt = build_prompt(task, study.conditions, condition)
    if mock:
        gen = _mock_generation(study.output_schema)
    else:
        gen = subject.generate(prompt, study.output_schema)
    subject_error = bool(gen.get("error"))
    output = gen.get("output", "")
    score = scorer.score(task, output, subject_error, mock=mock)
    scorer_error = bool(score.get("scorer_error", False))
    row: dict[str, Any] = {
        "question_id": question_id,
        "subset": subset,
        "condition": condition,
        "sample_id": sample_id,
        "error": subject_error or scorer_error,
        "subject_error": subject_error,
        "scorer_error": scorer_error,
        "returncode": gen.get("returncode", ""),
        "stderr": gen.get("stderr", ""),
        "stdout": gen.get("stdout", ""),
        "duration_s": gen.get("duration_s", 0.0),
        "prompt_hash": prompt_hash(prompt),
    }
    if isinstance(output, dict):
        row.update({k: v for k, v in output.items() if isinstance(k, str)})
        row["output_json"] = output
    else:
        row["output"] = output
    row.update(score)
    raw_path = raw_dir / _raw_name(condition, question_id, sample_id)
    with raw_path.open("w", encoding="utf-8") as f:
        json.dump({"task": task, "prompt": prompt, "row": row}, f, ensure_ascii=False, indent=2)
    return row


def _mock_generation(output_schema: dict[str, Any] | None) -> dict[str, Any]:
    if output_schema and "code" in output_schema.get("properties", {}):
        output: str | dict[str, Any] = {"code": "def mock_function(*args, **kwargs):\n    return None\n"}
    else:
        output = "わかりません。"
    return {
        "output": output,
        "error": False,
        "returncode": 0,
        "stderr": "",
        "stdout": "",
        "duration_s": 0.0,
    }


def _raw_name(condition: str, question_id: str, sample_id: int) -> str:
    safe_qid = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in question_id)
    return f"{condition}__{safe_qid}__{sample_id}.json"
