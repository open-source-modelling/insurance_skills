"""Rendering a Report for humans and for machines."""

from __future__ import annotations

import json
from dataclasses import asdict

from .findings import Outcome, Report
from .rules import RULES

_DESCRIPTIONS = {rule.id: rule.description for rule in RULES}


def render_text(report: Report, path: str, *, verbose: bool = False) -> str:
    lines = [f"{path}"]
    shown = report.findings if verbose else [
        f for f in report.findings if f.outcome is not Outcome.PASS
    ]

    last_rule = None
    for finding in shown:
        if finding.rule_id != last_rule:
            lines.append("")
            lines.append(f"  {finding.rule_id}  {_DESCRIPTIONS.get(finding.rule_id, '')}")
            last_rule = finding.rule_id
        lines.append(f"    {finding}")

    counts = report.counts()
    lines.append("")
    lines.append(
        f"  {counts['PASS']} passed, {counts['FAIL']} failed, {counts['N/A']} not applicable"
    )
    if not verbose and counts["FAIL"] == 0:
        lines.append("  (all checks passed — run with --verbose to see them)")
    return "\n".join(lines)


def render_json(report: Report, path: str) -> str:
    payload = {
        "file": path,
        "ok": report.ok,
        "counts": report.counts(),
        "findings": [
            {**asdict(f), "outcome": f.outcome.value, "examples": list(f.examples)}
            for f in report.findings
        ],
    }
    return json.dumps(payload, indent=2)
