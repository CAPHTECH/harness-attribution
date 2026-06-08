from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_POC_SRC = Path(__file__).resolve().parents[1] / "poc" / "src"
if str(_POC_SRC) not in sys.path:
    sys.path.insert(0, str(_POC_SRC))
import stats  # type: ignore  # noqa: E402

from .config import CONDITIONS


NORMALIZED_PRIMARY = "__primary_bad"


def analyze_rows(
    rows: list[dict[str, Any]],
    cfg: dict[str, Any],
    event_names: list[str],
    seed: int,
    conditions: tuple[str, ...] = CONDITIONS,
    condition_roles: dict[str, Any] | None = None,
    screen: bool = False,
) -> dict[str, Any]:
    primary_event = str(cfg["primary_event"])
    polarity = str(cfg.get("polarity", "minimize"))
    B = int(cfg.get("bootstrap_B", 2000))
    baselines = list(cfg.get("baselines", []))
    secondary_events = list(cfg.get("secondary_events", []))
    rng = np.random.default_rng(seed)

    normalized_rows = _with_normalized_primary(rows, primary_event, polarity)
    primary_subset = _first_subset_with_event(rows, primary_event)
    all_events = _unique([primary_event, *secondary_events, *event_names])
    factual_condition = _screen_role(condition_roles or {}, "factual") if screen else "H1"

    rates = []
    for event in all_events:
        for subset in _subsets_for_event(rows, event):
            for condition in conditions:
                p = stats.rate(rows, subset, condition, event)
                ci = stats.bootstrap_rate_ci(rows, subset, condition, event, B, rng)
                n = sum(
                    stats.event_value(row, event) is not None
                    for row in rows
                    if row.get("subset") == subset and row.get("condition") == condition
                )
                rates.append(
                    {
                        "subset": subset,
                        "condition": condition,
                        "event": event,
                        "n": n,
                        "rate": p,
                        "ci": ci,
                    }
                )

    contrasts = []
    for event in all_events:
        for subset in _subsets_for_event(rows, event):
            for baseline in baselines:
                if baseline not in conditions:
                    continue
                p1 = stats.rate(rows, subset, factual_condition, event)
                p0 = stats.rate(rows, subset, baseline, event)
                rrafpf = stats.rr_af_pf(p1, p0)
                contrasts.append(
                    {
                        "subset": subset,
                        "event": event,
                        "condition": factual_condition,
                        "baseline": baseline,
                        "RD": None if p1 is None or p0 is None else p1 - p0,
                        "RD_CI": stats.bootstrap_diff_ci(
                            rows, subset, factual_condition, baseline, event, B, rng
                        ),
                        "RR": rrafpf["RR"],
                        "AF": rrafpf["AF"],
                        "PF": rrafpf["PF"],
                    }
                )

    errors = sum(stats.as_bool(row.get("error")) is True for row in rows)
    analysis = {
        "rates": rates,
        "contrasts": contrasts,
        "errors": errors,
        "rows": len(rows),
        "primary_event": primary_event,
        "primary_subset": primary_subset,
        "polarity": polarity,
        "screen": screen,
    }
    if screen:
        analysis["triage"] = screen_triage(
            normalized_rows, cfg, primary_event, primary_subset, condition_roles or {}
        )
    else:
        analysis["verdict"] = _verdict(
            normalized_rows, rows, primary_subset, B, seed, baselines, secondary_events
        )
    return analysis


def screen_triage(
    normalized_rows: list[dict[str, Any]],
    cfg: dict[str, Any],
    primary_event: str,
    subset: str,
    condition_roles: dict[str, Any],
) -> dict[str, Any]:
    screen_cfg = cfg.get("screen", {})
    floor = float(screen_cfg.get("floor", 0.1)) if isinstance(screen_cfg, dict) else 0.1
    margin = float(screen_cfg.get("margin", 0.2)) if isinstance(screen_cfg, dict) else 0.2
    factual = _screen_role(condition_roles, "factual")
    ablate = _screen_role(condition_roles, "ablate")
    envelope = _screen_role(condition_roles, "envelope")

    e_f = stats.rate(normalized_rows, subset, factual, NORMALIZED_PRIMARY)
    e_a = stats.rate(normalized_rows, subset, ablate, NORMALIZED_PRIMARY)
    e_e = stats.rate(normalized_rows, subset, envelope, NORMALIZED_PRIMARY)
    rd_vs_ablate = None if e_f is None or e_a is None else e_f - e_a
    rd_vs_envelope = None if e_f is None or e_e is None else e_f - e_e
    rr_vs_ablate = stats.rr_af_pf(e_f, e_a)["RR"]
    rr_vs_envelope = stats.rr_af_pf(e_f, e_e)["RR"]

    rates = [e_f, e_a, e_e]
    if any(value is None for value in rates):
        label = "inconclusive"
        recommendation = "この規模では不確定。n/R を増やすか フル検証へ"
    elif e_a < floor or min(e_f, e_a, e_e) > 1 - floor:
        label = "no_headroom"
        recommendation = (
            "headroom なし：この課題/被検体では効果が観測できない。"
            "課題難度か被検体を見直せ"
        )
    elif e_f <= e_a - margin and e_f <= e_e - margin:
        label = "candidate_signal"
        recommendation = (
            "シグナルあり：要因が同長フィラーも超えて効いている可能性。"
            "フル検証（独立判定＋事前登録＋n×R＋bootstrap）を推奨"
        )
    elif abs(e_f - e_e) < margin:
        label = "surface_only"
        recommendation = (
            "シグナルなし：要因の\"意味\"でなく長さ/表層の可能性。"
            "フル検証は優先度低"
        )
    else:
        label = "inconclusive"
        recommendation = "この規模では不確定。n/R を増やすか フル検証へ"

    return {
        "label": label,
        "recommendation": recommendation,
        "e_factual": e_f,
        "e_ablate": e_a,
        "e_envelope": e_e,
        "rd_vs_ablate": rd_vs_ablate,
        "rr_vs_ablate": rr_vs_ablate,
        "rd_vs_envelope": rd_vs_envelope,
        "rr_vs_envelope": rr_vs_envelope,
        "primary_event": primary_event,
        "primary_subset": subset,
    }


def _screen_role(condition_roles: dict[str, Any], role: str) -> str:
    value = condition_roles.get(role)
    if value not in CONDITIONS:
        raise ValueError(
            f"screen mode requires conditions.{role} to name one of {CONDITIONS}"
        )
    return str(value)


def _with_normalized_primary(
    rows: list[dict[str, Any]], primary_event: str, polarity: str
) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        copy = dict(row)
        value = stats.as_bool(row.get(primary_event))
        if value is None:
            copy[NORMALIZED_PRIMARY] = ""
        elif polarity == "maximize":
            copy[NORMALIZED_PRIMARY] = "false" if value else "true"
        else:
            copy[NORMALIZED_PRIMARY] = "true" if value else "false"
        normalized.append(copy)
    return normalized


def _verdict(
    normalized_rows: list[dict[str, Any]],
    original_rows: list[dict[str, Any]],
    subset: str,
    B: int,
    seed: int,
    baselines: list[str],
    secondary_events: list[str],
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    bad_rates = {
        condition: stats.rate(normalized_rows, subset, condition, NORMALIZED_PRIMARY)
        for condition in CONDITIONS
    }
    h1_base_ci = stats.bootstrap_diff_ci(
        normalized_rows, subset, "H1", "H_base_len", NORMALIZED_PRIMARY, B, rng
    )
    h1_para_ci = stats.bootstrap_diff_ci(
        normalized_rows, subset, "H1", "H_para", NORMALIZED_PRIMARY, B, rng
    )
    present = [v for v in bad_rates.values() if v is not None and not math.isnan(v)]
    no_observations = not present
    all_same = bool(present) and (max(present) - min(present) <= 1e-12)
    contra_max = (
        bad_rates.get("H_contra") is not None
        and present
        and bad_rates["H_contra"] == max(present)
    )
    if no_observations:
        primary = "inconclusive"
    elif all_same:
        primary = "inconclusive"
    elif stats.ci_less_than_zero(h1_base_ci) and stats.ci_contains_zero(h1_para_ci) and contra_max:
        primary = "meaning_attributable"
    elif stats.ci_contains_zero(h1_base_ci):
        primary = "surface_confound"
    elif not stats.ci_contains_zero(h1_para_ci):
        primary = "fragile"
    else:
        primary = "inconclusive"

    tradeoff_details = _tradeoffs(original_rows, baselines, secondary_events, B, seed)
    tradeoff = any(item["worse"] for item in tradeoff_details)
    verdict_str = primary
    if no_observations:
        verdict_str = "inconclusive (no usable primary observations)"
    elif all_same:
        verdict_str = "inconclusive (floor/ceiling: all primary rates are equal)"
    elif tradeoff:
        verdict_str = f"{primary}+tradeoff_flag"
    return {
        "primary": primary,
        "tradeoff_flag": tradeoff,
        "tradeoff_details": tradeoff_details,
        "no_observations": no_observations,
        "floor_ceiling": all_same,
        "verdict": verdict_str,
        "bad_primary_rates": bad_rates,
        "h1_minus_base_len_ci": h1_base_ci,
        "h1_minus_para_ci": h1_para_ci,
        "contra_is_max_bad_primary": contra_max,
    }


def _tradeoffs(
    rows: list[dict[str, Any]],
    baselines: list[str],
    secondary_events: list[str],
    B: int,
    seed: int,
) -> list[dict[str, Any]]:
    details = []
    for event in secondary_events:
        for subset in _subsets_for_event(rows, event):
            for baseline in baselines:
                rng = np.random.default_rng(seed)
                ci = stats.bootstrap_diff_ci(rows, subset, "H1", baseline, event, B, rng)
                if _event_is_beneficial(event):
                    worse = stats.ci_less_than_zero(ci)
                    direction = "decrease"
                else:
                    worse = stats.ci_greater_than_zero(ci)
                    direction = "increase"
                details.append(
                    {
                        "subset": subset,
                        "event": event,
                        "baseline": baseline,
                        "RD_CI": ci,
                        "worse_direction": direction,
                        "worse": worse,
                    }
                )
    return details


def _event_is_beneficial(event: str) -> bool:
    text = event.lower()
    return any(token in text for token in ("correct", "passed", "pass", "compile", "abstention"))


def _first_subset_with_event(rows: list[dict[str, Any]], event: str) -> str:
    subsets = _subsets_for_event(rows, event)
    return subsets[0] if subsets else "all"


def _subsets_for_event(rows: list[dict[str, Any]], event: str) -> list[str]:
    subsets = sorted(
        {
            str(row.get("subset", "all"))
            for row in rows
            if stats.event_value(row, event) is not None
        }
    )
    if subsets:
        return subsets
    return sorted({str(row.get("subset", "all")) for row in rows})


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


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
