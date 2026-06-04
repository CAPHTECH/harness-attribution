from __future__ import annotations

# Harness Attribution PoC II — subject: code generation (factor = detailed spec).
# Subject = codex CLI (gpt-5.5) one-shot, output-schema {code}. Scorer = EXECUTION
# of hidden tests in an isolated subprocess (deterministic; no LLM judge -> no scorer bias).
# Primary event E_fail = generated code does NOT pass the hidden test suite.

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stats  # noqa: E402  (rate / bootstrap_diff_ci / ci_* reuse)

CONDITIONS = ("H1", "H0_ablate", "H0_neutral", "H_base_len", "H_para", "H_contra")
# Length-matched, behaviour-IRRELEVANT filler for the envelope baseline.
FILLER = ("General tips: choose clear variable names, add a short docstring, follow "
          "PEP 8 style, and prefer readable code over clever one-liners.")

CODE_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"code": {"type": "string"}}, "required": ["code"]}


def load_tasks(path: str) -> list[dict[str, Any]]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def build_prompt(task: dict[str, Any], condition: str) -> str:
    base = (f"Write a Python function with this signature:\n{task['signature']}\n"
            "Return the complete function definition (plus any needed imports) in the 'code' field. "
            "Do not include tests or explanation.")
    extra = {
        "H1": f"\n\nSpecification: {task['spec']}",
        "H0_ablate": "",
        "H0_neutral": "\n\nImplement a correct, robust, general-purpose version.",
        "H_base_len": f"\n\n{FILLER}",
        "H_para": f"\n\nSpecification: {task['para_spec']}",
        "H_contra": f"\n\nSpecification: {task['contra_spec']}",
    }[condition]
    return base + extra


def generate_code(prompt: str, model: str | None) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codex-codegen-") as scratch:
        schema_file = os.path.join(scratch, "schema.json")
        json.dump(CODE_SCHEMA, open(schema_file, "w"))
        cmd = ["codex", "exec", "--output-schema", schema_file, "-s", "read-only",
               "--skip-git-repo-check", "--ignore-user-config", "-C", scratch]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        with open(os.devnull, "rb") as devnull:
            proc = subprocess.run(cmd, stdin=devnull, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
    dur = time.perf_counter() - started
    if proc.returncode != 0:
        return {"code": "", "error": True, "dur": dur}
    try:
        code = json.loads(proc.stdout.strip()).get("code", "")
    except json.JSONDecodeError:
        return {"code": "", "error": True, "dur": dur}
    return {"code": code, "error": not bool(code.strip()), "dur": dur}


CHECK_TEMPLATE = """\
import sys
try:
    import solution
except Exception as e:
    sys.stderr.write("IMPORT_ERROR:%r" % e); sys.exit(3)
if not hasattr(solution, {name!r}):
    sys.stderr.write("NO_FUNC"); sys.exit(4)
try:
    exec({tests!r}, solution.__dict__)
except Exception as e:
    sys.stderr.write("TEST_FAIL:%r" % e); sys.exit(5)
print("OK"); sys.exit(0)
"""


def score_code(code: str, name: str, tests: list[str], timeout: int = 15) -> dict[str, bool]:
    """Run hidden tests in an isolated subprocess. Returns {compile, passed}."""
    with tempfile.TemporaryDirectory(prefix="codegen-run-") as d:
        open(os.path.join(d, "solution.py"), "w", encoding="utf-8").write(code)
        check = CHECK_TEMPLATE.format(name=name, tests="\n".join(tests))
        open(os.path.join(d, "check.py"), "w", encoding="utf-8").write(check)
        try:
            p = subprocess.run([sys.executable, "-S", "check.py"], cwd=d,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            return {"compile": True, "passed": False}  # ran but hung -> fail
    if rc == 0:
        return {"compile": True, "passed": True}
    if rc in (3, 4):
        return {"compile": False, "passed": False}   # import error / function missing
    if rc == 5:
        return {"compile": True, "passed": False}     # test assertion/exception
    return {"compile": False, "passed": False}        # unexpected crash


def run_one(task: dict[str, Any], condition: str, sample_id: int, model: str | None,
            raw_dir: Path) -> dict[str, Any]:
    gen = generate_code(build_prompt(task, condition), model)
    if gen["error"]:
        score = {"compile": False, "passed": False}
    else:
        score = score_code(gen["code"], task["name"], task["tests"])
    row = {
        "question_id": task["id"], "subset": "code", "condition": condition,
        "sample_id": sample_id, "name": task["name"],
        "E_fail": "" if gen["error"] else ("false" if score["passed"] else "true"),
        "E_compile": "" if gen["error"] else ("true" if score["compile"] else "false"),
        "error": "true" if gen["error"] else "false",
        "code": gen["code"], "dur": gen["dur"],
    }
    json.dump(row, open(raw_dir / f"{condition}__{task['id']}__{sample_id}.json", "w",
                        encoding="utf-8"), ensure_ascii=False, indent=2)
    return row


def bool_rate(rows, cond, event):
    r = stats.rate(rows, "code", cond, event)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="poc/codegen/tasks.jsonl")
    ap.add_argument("--out", default="poc/results_codegen")
    ap.add_argument("--model", default=None)
    ap.add_argument("--R", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--bootstrap-B", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()

    start = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    tasks = load_tasks(args.tasks)
    if args.limit is not None:
        tasks = tasks[:args.limit]
    out = Path(args.out); raw = out / "raw"; raw.mkdir(parents=True, exist_ok=True)

    jobs = [(t, c, s) for t in tasks for c in CONDITIONS for s in range(1, args.R + 1)]
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        rows = list(ex.map(lambda j: run_one(j[0], j[1], j[2], args.model, raw), jobs))

    fields = ["question_id", "subset", "condition", "sample_id", "name",
              "E_fail", "E_compile", "error", "dur"]
    with open(out / "scored.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    # analysis
    rng = np.random.default_rng(args.seed)
    B = args.bootstrap_B
    u = {c: bool_rate(rows, c, "E_fail") for c in CONDITIONS}
    comp = {c: bool_rate(rows, c, "E_compile") for c in CONDITIONS}
    h1_base = stats.bootstrap_diff_ci(rows, "code", "H1", "H_base_len", "E_fail", B, rng)
    h1_para = stats.bootstrap_diff_ci(rows, "code", "H1", "H_para", "E_fail", B, rng)
    h1_ablate = stats.bootstrap_diff_ci(rows, "code", "H1", "H0_ablate", "E_fail", B, rng)
    present = [v for v in u.values() if v is not None]
    max_u = max(present) if present else 0.0
    floor = max_u == 0.0
    contra_max = u.get("H_contra") is not None and u["H_contra"] == max(
        v for v in u.values() if v is not None)
    if floor:
        primary = "inconclusive"
    elif stats.ci_less_than_zero(h1_base) and stats.ci_contains_zero(h1_para) and contra_max:
        primary = "meaning_attributable"
    elif stats.ci_contains_zero(h1_base):
        primary = "surface_confound"
    elif not stats.ci_contains_zero(h1_para):
        primary = "fragile"
    else:
        primary = "inconclusive"

    err = sum(r["error"] == "true" for r in rows)
    lines = ["# Codegen report (factor = detailed spec)\n", "## E_fail rate (lower=better for H1)\n",
             "| condition | E_fail | 95% CI | E_compile |", "| --- | ---: | --- | ---: |"]
    for c in CONDITIONS:
        ci = stats.bootstrap_rate_ci(rows, "code", c, "E_fail", B, rng)
        ci_s = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "NA"
        lines.append(f"| {c} | {u[c]:.3f} | {ci_s} | {comp[c]:.3f} |")
    lines += ["\n## Contrasts on E_fail (H1 - baseline)\n",
              f"- H1 - H0_ablate: {h1_ablate}", f"- H1 - H_base_len: {h1_base}",
              f"- H1 - H_para: {h1_para}",
              "\n## Verdict\n",
              f"- verdict: `{primary}`", f"- H_contra is max E_fail: {contra_max}",
              f"- u_rates: {{{', '.join(f'{c}:{u[c]:.3f}' for c in CONDITIONS)}}}",
              f"\n## Exclusions\n- generation errors excluded: {err} / {len(rows)}"]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    meta = {"subject": "codex", "model": args.model, "R": args.R, "tasks": len(tasks),
            "total_calls": len(rows), "error_count": err,
            "started_at": start, "ended_at": datetime.now(timezone.utc).isoformat(),
            "verdict": primary, "u_rates": u}
    json.dump(meta, open(out / "run_meta.json", "w"), ensure_ascii=False, indent=2)
    print(f"wrote {out} in {time.perf_counter()-t0:.1f}s; verdict={primary}; errors={err}/{len(rows)}")


if __name__ == "__main__":
    main()
