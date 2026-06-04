from __future__ import annotations

import csv
import math
from collections import defaultdict
from typing import Any

import numpy as np


CONDITIONS = ("H1", "H0_ablate", "H0_neutral", "H_base_len", "H_para", "H_contra")
EVENTS_BY_SUBSET = {
    "unanswerable": ("E_unsupported", "E_abstention"),
    "answerable": ("E_correct", "E_over_refusal"),
}
BASELINES = ("H0_ablate", "H_base_len")


def read_scored_csv(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def as_bool(value: Any) -> bool | None:
    if value in (True, False):
        return bool(value)
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def event_value(row: dict[str, Any], event: str) -> int | None:
    if as_bool(row.get("error")):
        return None
    value = as_bool(row.get(event, ""))
    if value is None:
        return None
    return int(value)


def rate(rows: list[dict[str, Any]], subset: str, condition: str, event: str) -> float | None:
    vals = [
        event_value(row, event)
        for row in rows
        if row.get("subset") == subset and row.get("condition") == condition
    ]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return float(np.mean(vals))


def _cluster_index(rows: list[dict[str, Any]], subset: str) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("subset") == subset:
            index[row["question_id"]].append(row)
    return index


def _mean_for_sample(
    index: dict[str, list[dict[str, Any]]],
    question_ids: list[str],
    condition: str,
    event: str,
) -> float:
    vals: list[int] = []
    for qid in question_ids:
        for row in index.get(qid, []):
            if row.get("condition") != condition:
                continue
            value = event_value(row, event)
            if value is not None:
                vals.append(value)
    return float(np.mean(vals)) if vals else math.nan


def bootstrap_rate_ci(
    rows: list[dict[str, Any]],
    subset: str,
    condition: str,
    event: str,
    B: int,
    rng: np.random.Generator,
) -> tuple[float, float] | None:
    index = _cluster_index(rows, subset)
    qids = sorted(index)
    if not qids or B <= 0:
        return None
    draws = []
    for _ in range(B):
        sampled = list(rng.choice(qids, size=len(qids), replace=True))
        draws.append(_mean_for_sample(index, sampled, condition, event))
    draws = np.asarray([x for x in draws if not math.isnan(x)])
    if len(draws) == 0:
        return None
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def bootstrap_diff_ci(
    rows: list[dict[str, Any]],
    subset: str,
    condition: str,
    baseline: str,
    event: str,
    B: int,
    rng: np.random.Generator,
) -> tuple[float, float] | None:
    index = _cluster_index(rows, subset)
    qids = sorted(index)
    if not qids or B <= 0:
        return None
    draws = []
    for _ in range(B):
        sampled = list(rng.choice(qids, size=len(qids), replace=True))
        p1 = _mean_for_sample(index, sampled, condition, event)
        p0 = _mean_for_sample(index, sampled, baseline, event)
        if not math.isnan(p1) and not math.isnan(p0):
            draws.append(p1 - p0)
    if not draws:
        return None
    lo, hi = np.percentile(np.asarray(draws), [2.5, 97.5])
    return float(lo), float(hi)


def ci_contains_zero(ci: tuple[float, float] | None) -> bool:
    return bool(ci and ci[0] <= 0 <= ci[1])


def ci_less_than_zero(ci: tuple[float, float] | None) -> bool:
    return bool(ci and ci[1] < 0)


def ci_greater_than_zero(ci: tuple[float, float] | None) -> bool:
    return bool(ci and ci[0] > 0)


def rr_af_pf(p1: float | None, p0: float | None) -> dict[str, float | None]:
    if p1 is None or p0 is None or p0 == 0:
        return {"RR": None, "AF": None, "PF": None}
    rr = p1 / p0
    if rr > 1:
        return {"RR": rr, "AF": 1 - (1 / rr), "PF": None}
    if rr < 1:
        return {"RR": rr, "AF": None, "PF": 1 - rr}
    return {"RR": rr, "AF": 0.0, "PF": 0.0}


def mixed_effect_logistic(rows: list[dict[str, Any]]) -> str:
    try:
        import pandas as pd
        from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
    except ImportError:
        return "statsmodels unavailable; mixed-effect logistic skipped."

    data = []
    for row in rows:
        if row.get("subset") != "unanswerable":
            continue
        value = event_value(row, "E_unsupported")
        if value is None:
            continue
        data.append(
            {
                "y": value,
                "condition": row["condition"],
                "question_id": row["question_id"],
            }
        )
    if not data:
        return "no usable rows; mixed-effect logistic skipped."
    try:
        df = pd.DataFrame(data)
        model = BinomialBayesMixedGLM.from_formula(
            "y ~ C(condition)",
            {"question": "0 + C(question_id)"},
            df,
        )
        fit = model.fit_vb()
        return str(fit.summary())
    except Exception as exc:
        return f"mixed-effect logistic failed: {exc}"


def verdict(rows: list[dict[str, Any]], B: int, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    u = {c: rate(rows, "unanswerable", c, "E_unsupported") for c in CONDITIONS}
    h1_base_ci = bootstrap_diff_ci(
        rows, "unanswerable", "H1", "H_base_len", "E_unsupported", B, rng
    )
    h1_para_ci = bootstrap_diff_ci(
        rows, "unanswerable", "H1", "H_para", "E_unsupported", B, rng
    )
    h1_ablate_over_refusal_ci = bootstrap_diff_ci(
        rows, "answerable", "H1", "H0_ablate", "E_over_refusal", B, rng
    )
    h1_ablate_correct_ci = bootstrap_diff_ci(
        rows, "answerable", "H1", "H0_ablate", "E_correct", B, rng
    )

    present = [v for v in u.values() if v is not None]
    max_u = max(present) if present else 0.0
    floor_no_events = max_u == 0.0
    contra_max = (
        u.get("H_contra") is not None
        and u["H_contra"] == max(v for v in u.values() if v is not None)
    )
    if floor_no_events:
        # No unsupported-claim events under ANY condition: nothing to attribute.
        # This is a floor, not evidence that H1 == H_base_len (surface confound).
        primary = "inconclusive"
    elif ci_less_than_zero(h1_base_ci) and ci_contains_zero(h1_para_ci) and contra_max:
        primary = "meaning_attributable"
    elif ci_contains_zero(h1_base_ci):
        primary = "surface_confound"
    elif not ci_contains_zero(h1_para_ci):
        primary = "fragile"
    else:
        primary = "inconclusive"

    tradeoff = ci_greater_than_zero(h1_ablate_over_refusal_ci) or ci_less_than_zero(
        h1_ablate_correct_ci
    )
    verdict_str = primary
    if floor_no_events:
        verdict_str = "inconclusive (floor: no E_unsupported events under any condition)"
    elif tradeoff:
        verdict_str = f"{primary}+tradeoff_flag"
    return {
        "primary": primary,
        "tradeoff_flag": tradeoff,
        "floor_no_events": floor_no_events,
        "verdict": verdict_str,
        "u_rates": u,
        "h1_minus_base_len_ci": h1_base_ci,
        "h1_minus_para_ci": h1_para_ci,
        "h1_minus_ablate_over_refusal_ci": h1_ablate_over_refusal_ci,
        "h1_minus_ablate_correct_ci": h1_ablate_correct_ci,
    }


def analyze(scored_csv: str, config: dict[str, Any]) -> dict[str, Any]:
    rows = read_scored_csv(scored_csv)
    B = int(config.get("bootstrap_B", 2000))
    seed = int(config.get("seed", 12345))
    rng = np.random.default_rng(seed)

    rate_table = []
    for subset, events in EVENTS_BY_SUBSET.items():
        for condition in CONDITIONS:
            for event in events:
                p = rate(rows, subset, condition, event)
                ci = bootstrap_rate_ci(rows, subset, condition, event, B, rng)
                n = sum(
                    event_value(row, event) is not None
                    for row in rows
                    if row.get("subset") == subset and row.get("condition") == condition
                )
                rate_table.append(
                    {
                        "subset": subset,
                        "condition": condition,
                        "event": event,
                        "rate": p,
                        "ci": ci,
                        "n": n,
                    }
                )

    contrasts = []
    for subset, events in EVENTS_BY_SUBSET.items():
        for event in events:
            for baseline in BASELINES:
                for condition in CONDITIONS:
                    if condition == baseline:
                        continue
                    p1 = rate(rows, subset, condition, event)
                    p0 = rate(rows, subset, baseline, event)
                    rd = None if p1 is None or p0 is None else p1 - p0
                    ci = bootstrap_diff_ci(rows, subset, condition, baseline, event, B, rng)
                    ratio = rr_af_pf(p1, p0)
                    contrasts.append(
                        {
                            "subset": subset,
                            "event": event,
                            "condition": condition,
                            "baseline": baseline,
                            "RD": rd,
                            "RD_CI": ci,
                            **ratio,
                        }
                    )

    errors = sum(as_bool(row.get("error")) is True for row in rows)
    return {
        "rates": rate_table,
        "contrasts": contrasts,
        "verdict": verdict(rows, B, seed),
        "errors": errors,
        "rows": len(rows),
        "mixed_effect_logistic": mixed_effect_logistic(rows),
    }


def fmt(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, tuple):
        return f"[{fmt(value[0])}, {fmt(value[1])}]"
    if isinstance(value, float):
        if math.isnan(value):
            return "NA"
        return f"{value:.3f}"
    return str(value)


def render_report(analysis: dict[str, Any]) -> str:
    lines = [
        "# PoC report",
        "",
        "## Rates",
        "",
        "| subset | condition | event | n | rate | bootstrap 95% CI |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in analysis["rates"]:
        lines.append(
            f"| {row['subset']} | {row['condition']} | {row['event']} | "
            f"{row['n']} | {fmt(row['rate'])} | {fmt(row['ci'])} |"
        )

    lines.extend(
        [
            "",
            "## Contrasts",
            "",
            "| subset | event | condition | baseline | RD | RD 95% CI | RR | AF | PF |",
            "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for row in analysis["contrasts"]:
        lines.append(
            f"| {row['subset']} | {row['event']} | {row['condition']} | {row['baseline']} | "
            f"{fmt(row['RD'])} | {fmt(row['RD_CI'])} | {fmt(row['RR'])} | "
            f"{fmt(row['AF'])} | {fmt(row['PF'])} |"
        )

    reliability = analysis.get("reliability", {})
    reliability_rate = reliability.get("agreement_rate")
    reliability_warning = (
        "- scorer-bias warning: deterministic agreement is low; inspect judge outputs."
        if reliability_rate is not None and reliability_rate < 0.8
        else "- scorer-bias warning: none from deterministic agreement threshold."
    )
    verdict_data = analysis["verdict"]
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"- verdict: `{verdict_data['verdict']}`",
            f"- primary: `{verdict_data['primary']}`",
            f"- tradeoff_flag: `{verdict_data['tradeoff_flag']}`",
            f"- H1 - H_base_len CI on E_unsupported: {fmt(verdict_data['h1_minus_base_len_ci'])}",
            f"- H1 - H_para CI on E_unsupported: {fmt(verdict_data['h1_minus_para_ci'])}",
            "",
            "## Reliability proxy",
            "",
            f"- deterministic abstention agreement: {fmt(reliability.get('agreement_rate'))} "
            f"({reliability.get('agree', 0)}/{reliability.get('total', 0)})",
            reliability_warning,
            "",
            "## Exclusions",
            "",
            f"- error rows excluded from rate calculations: {analysis['errors']} / {analysis['rows']}",
            "",
            "## Mixed-effect logistic",
            "",
            "```text",
            analysis["mixed_effect_logistic"],
            "```",
            "",
            "## Notes",
            "",
            "- If the subject and judge use the same base model, hyp-scorer-bias may remain. This run uses blinding plus deterministic abstention cross-check as mitigation; human audit is still recommended.",
            "- hyp-interaction-only is outside this PoC because no 2x2 arm was run.",
        ]
    )
    return "\n".join(lines) + "\n"
