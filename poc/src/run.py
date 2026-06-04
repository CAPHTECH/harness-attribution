from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

import judge
import stats
import subject


CSV_FIELDS = [
    "question_id",
    "subset",
    "category",
    "condition",
    "sample_id",
    "output",
    "is_abstention",
    "is_confident_specific_claim",
    "is_refusal",
    "answer_matches_reference",
    "E_unsupported",
    "E_abstention",
    "E_over_refusal",
    "E_correct",
    "error",
]


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_questions(path: str, n_per_subset: int | None) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    questions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            subset = row["subset"]
            counts.setdefault(subset, 0)
            if n_per_subset is not None and counts[subset] >= n_per_subset:
                continue
            counts[subset] += 1
            questions.append(row)
    return questions


def codex_version(mock: bool) -> str | None:
    if mock:
        return "mock"
    proc = subprocess.run(
        ["codex", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def raw_path(raw_dir: Path, condition: str, qid: str, sample_id: int) -> Path:
    return raw_dir / f"{condition}__{qid}__{sample_id}.json"


def load_raw_records(raw_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(raw_dir.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            records.append(json.load(f))
    return records


def mock_subject_output(question: dict[str, Any]) -> dict[str, Any]:
    if question["subset"] == "unanswerable":
        output = "わかりません。与えられた情報だけでは特定できません。"
    else:
        output = str(question.get("reference", ""))
    prompt = subject.build_prompt("H0_ablate", question["question"])
    return {
        "output": output,
        "error": False,
        "returncode": 0,
        "stderr": "",
        "stdout": "",
        "duration_s": 0.0,
        "prompt_hash": subject.prompt_hash(prompt),
    }


def generate_one(
    question: dict[str, Any],
    condition: str,
    sample_id: int,
    config: dict[str, Any],
    raw_dir: Path,
    mock: bool,
    version: str | None,
) -> dict[str, Any]:
    if mock:
        result = mock_subject_output(question)
        result["prompt_hash"] = subject.prompt_hash(
            subject.build_prompt(condition, question["question"])
        )
    else:
        result = subject.call_subject(
            question=question["question"],
            condition=condition,
            model=config.get("model"),
        )
    raw = {
        "question_id": question["id"],
        "subset": question["subset"],
        "category": question.get("category", ""),
        "question": question["question"],
        "reference": question.get("reference"),
        "condition": condition,
        "sample_id": sample_id,
        "output": result["output"],
        "error": result["error"],
        "returncode": result["returncode"],
        "stderr": result["stderr"],
        "stdout": result["stdout"],
        "usage": None,
        "duration_s": result["duration_s"],
        "prompt_hash": result["prompt_hash"],
        "codex_version": version,
    }
    with open(raw_path(raw_dir, condition, question["id"], sample_id), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    return raw


def generate(
    questions: list[dict[str, Any]],
    config: dict[str, Any],
    results_dir: Path,
    mock: bool,
    version: str | None,
) -> list[dict[str, Any]]:
    raw_dir = results_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    R = int(config.get("R", 3))
    workers = int(config.get("concurrency", 8))
    tasks = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for question in questions:
            for condition in subject.CONDITIONS:
                for sample_id in range(1, R + 1):
                    tasks.append(
                        executor.submit(
                            generate_one,
                            question,
                            condition,
                            sample_id,
                            config,
                            raw_dir,
                            mock,
                            version,
                        )
                    )
        return [future.result() for future in as_completed(tasks)]


def mock_judgment(raw: dict[str, Any]) -> dict[str, Any]:
    if raw["subset"] == "unanswerable":
        abstention = judge.deterministic_abstention(raw["output"])
        return {
            "is_abstention": abstention,
            "is_confident_specific_claim": not abstention,
            "is_refusal": abstention,
            "answer_matches_reference": "na",
            "rationale": "mock",
        }
    return {
        "is_abstention": False,
        "is_confident_specific_claim": True,
        "is_refusal": False,
        "answer_matches_reference": "true",
        "rationale": "mock",
    }


def bool_text(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def judge_one(raw: dict[str, Any], config: dict[str, Any], mock: bool) -> dict[str, Any]:
    if raw["error"]:
        return {
            "is_abstention": False,
            "is_confident_specific_claim": False,
            "is_refusal": False,
            "answer_matches_reference": "na",
            "rationale": "subject_error",
        }
    if mock:
        return mock_judgment(raw)
    return judge.call_judge(
        question=raw["question"],
        subset=raw["subset"],
        answer=raw["output"],
        model=config.get("judge_model"),
    )


def score(raw_records: list[dict[str, Any]], config: dict[str, Any], scored_csv: Path, mock: bool) -> list[dict[str, Any]]:
    ordered = sorted(
        raw_records,
        key=lambda r: (r["question_id"], r["condition"], int(r["sample_id"])),
    )
    if mock:
        judgments = [judge_one(raw, config, mock) for raw in ordered]
    else:
        workers = int(config.get("concurrency", 8))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            judgments = list(executor.map(lambda raw: judge_one(raw, config, mock), ordered))

    rows = []
    for raw, judgment in zip(ordered, judgments):
        is_abstention = bool(judgment["is_abstention"])
        is_claim = bool(judgment["is_confident_specific_claim"])
        is_refusal = bool(judgment["is_refusal"])
        answer_matches = judgment["answer_matches_reference"]

        e_unsupported = e_abstention = e_over_refusal = e_correct = None
        if not raw["error"] and raw["subset"] == "unanswerable":
            e_unsupported = is_claim and not is_abstention
            e_abstention = is_abstention
        if not raw["error"] and raw["subset"] == "answerable":
            e_correct = answer_matches == "true"
            e_over_refusal = is_refusal or is_abstention

        rows.append(
            {
                "question_id": raw["question_id"],
                "subset": raw["subset"],
                "category": raw["category"],
                "condition": raw["condition"],
                "sample_id": raw["sample_id"],
                "output": raw["output"],
                "is_abstention": bool_text(is_abstention),
                "is_confident_specific_claim": bool_text(is_claim),
                "is_refusal": bool_text(is_refusal),
                "answer_matches_reference": answer_matches,
                "E_unsupported": bool_text(e_unsupported),
                "E_abstention": bool_text(e_abstention),
                "E_over_refusal": bool_text(e_over_refusal),
                "E_correct": bool_text(e_correct),
                "error": bool_text(bool(raw["error"])),
            }
        )

    with open(scored_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_run_meta(
    path: Path,
    start_time: str,
    end_time: str,
    config: dict[str, Any],
    raw_records: list[dict[str, Any]],
    scored_rows: list[dict[str, Any]],
    version: str | None,
    mock: bool,
) -> None:
    subject_calls = len(raw_records)
    judge_calls = 0 if mock else sum(row["error"] != "true" for row in scored_rows)
    meta = {
        "codex_version": version,
        "model": config.get("model"),
        "judge_model": config.get("judge_model"),
        "config": config,
        "total_calls": subject_calls + judge_calls,
        "total_subject_calls": subject_calls,
        "total_judge_calls": judge_calls,
        "error_count": sum(row["error"] == "true" for row in scored_rows),
        "started_at": start_time,
        "ended_at": end_time,
        "prompt_hashes": sorted({raw["prompt_hash"] for raw in raw_records}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--rescore", action="store_true",
                        help="re-judge existing results/raw outputs without regenerating subjects")
    parser.add_argument("--judge-model", default=None,
                        help="override config judge_model (e.g. a different model to test scorer bias)")
    args = parser.parse_args()

    start = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    config = load_config(args.config)
    if args.limit is not None:
        config["n_per_subset"] = args.limit
    if args.judge_model is not None:
        config["judge_model"] = args.judge_model

    results_dir = Path(config["paths"]["results"])
    results_dir.mkdir(parents=True, exist_ok=True)
    version = codex_version(args.mock)

    if args.rescore:
        raw_records = load_raw_records(results_dir / "raw")
    else:
        questions = load_questions(config["paths"]["questions"], config.get("n_per_subset"))
        raw_records = generate(questions, config, results_dir, args.mock, version)
    scored_rows = score(raw_records, config, results_dir / "scored.csv", args.mock)
    analysis = stats.analyze(str(results_dir / "scored.csv"), config)
    analysis["reliability"] = judge.abstention_agreement(scored_rows)
    with open(results_dir / "report.md", "w", encoding="utf-8") as f:
        f.write(stats.render_report(analysis))

    end = datetime.now(timezone.utc).isoformat()
    write_run_meta(
        results_dir / "run_meta.json",
        start,
        end,
        config,
        raw_records,
        scored_rows,
        version,
        args.mock,
    )
    elapsed = time.perf_counter() - started
    print(f"wrote {results_dir} in {elapsed:.2f}s")


if __name__ == "__main__":
    main()
