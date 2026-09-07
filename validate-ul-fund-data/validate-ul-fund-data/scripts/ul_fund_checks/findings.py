"""The result vocabulary: what a check is allowed to say.

Three outcomes, not two. A rule that does not apply (an optional column that
is absent) is N/A, which is different from a rule that applied and passed.
Collapsing the two produces reports people stop reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: Cap on example cell references carried in a single finding. A finding is a
#: pointer to where to look, not a dump of every offending row.
MAX_EXAMPLES = 5


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "N/A"


@dataclass(frozen=True)
class Finding:
    """One rule, evaluated against one scope (a workbook, sheet, or column)."""

    rule_id: str
    outcome: Outcome
    message: str
    sheet: str | None = None
    column: str | None = None
    examples: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        parts = [p for p in (self.sheet, self.column) if p]
        return "!".join(parts) if parts else "<workbook>"

    def __str__(self) -> str:
        line = f"{self.outcome.value:<4} {self.rule_id:<4} {self.scope}: {self.message}"
        if self.examples:
            line += f"  (e.g. {', '.join(self.examples)})"
        return line


@dataclass
class Report:
    """Everything the run found, in the order it was found."""

    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings) -> None:
        self.findings.extend(findings)

    def of(self, outcome: Outcome) -> list[Finding]:
        return [f for f in self.findings if f.outcome is outcome]

    @property
    def failures(self) -> list[Finding]:
        return self.of(Outcome.FAIL)

    @property
    def ok(self) -> bool:
        return not self.failures

    def counts(self) -> dict[str, int]:
        return {o.value: len(self.of(o)) for o in Outcome}


def passed(rule_id: str, message: str, **scope) -> Finding:
    return Finding(rule_id, Outcome.PASS, message, **scope)


def failed(rule_id: str, message: str, examples=(), **scope) -> Finding:
    return Finding(
        rule_id, Outcome.FAIL, message, examples=tuple(examples)[:MAX_EXAMPLES], **scope
    )


def not_applicable(rule_id: str, message: str, **scope) -> Finding:
    return Finding(rule_id, Outcome.NA, message, **scope)
