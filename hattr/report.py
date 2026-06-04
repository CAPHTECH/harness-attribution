from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .analysis import fmt


def write_outputs(
    results_dir: Path,
    rows: list[dict[str, Any]],
    analysis: dict[str, Any],
    warnings: list[str],
    meta: dict[str, Any],
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_scored_csv(results_dir / "scored.csv", rows)
    (results_dir / "report.md").write_text(render_report(analysis, warnings), encoding="utf-8")
    with (results_dir / "run_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def render_report(analysis: dict[str, Any], warnings: list[str]) -> str:
    lines = ["# hattr report", ""]
    lines.extend(["## Guardrails", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Rates",
            "",
            "| subset | condition | event | n | rate | bootstrap 95% CI |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
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
            "| subset | event | condition | baseline | RD | RD 95% CI |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in analysis["contrasts"]:
        lines.append(
            f"| {row['subset']} | {row['event']} | {row['condition']} | "
            f"{row['baseline']} | {fmt(row['RD'])} | {fmt(row['RD_CI'])} |"
        )

    verdict = analysis["verdict"]
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"- verdict: `{verdict['verdict']}`",
            f"- primary: `{verdict['primary']}`",
            f"- primary_event: `{analysis['primary_event']}`",
            f"- primary_subset: `{analysis['primary_subset']}`",
            f"- polarity: `{analysis['polarity']}`",
            f"- tradeoff_flag: `{verdict['tradeoff_flag']}`",
            f"- H1 - H_base_len CI on normalized primary: {fmt(verdict['h1_minus_base_len_ci'])}",
            f"- H1 - H_para CI on normalized primary: {fmt(verdict['h1_minus_para_ci'])}",
            f"- H_contra is max normalized primary: {verdict['contra_is_max_bad_primary']}",
            "",
            "## Exclusions",
            "",
            f"- error rows excluded from rate calculations: {analysis['errors']} / {analysis['rows']}",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_scored_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _csv_value(value: Any) -> Any:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value

