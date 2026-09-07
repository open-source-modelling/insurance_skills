"""Proves every rule catches something, by running the skill end to end.

These tests deliberately do not call the checking library directly. They run
`validate.py` exactly as the skill does, then read the **report workbook the
analyst receives** and assert that the right row says FAIL. That is the thing
being relied on — a rule that fires correctly but never reaches the report
would still be a broken control.

`examples/bad/` holds one workbook per rule, each with exactly one defect.
The assertion is two-sided: the workbook must trip its own rule *and no
others*. A one-sided check would let a rule that fails on everything look
healthy.

Run from the skill root::

    python3 -m pytest tests/ -q
    python3 -m pytest tests/test_every_rule_fails.py -v    # one line per rule
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from make_fixtures import FIXTURES  # noqa: E402
from ul_fund_checks import RULES  # noqa: E402
from validate import main  # noqa: E402

CLEAN = ROOT / "examples" / "good" / "UL_FUND_DATA_Q4_2025.xlsx"
BAD_DIR = ROOT / "examples" / "bad"
FIRST_DATA_ROW = 9
STATUS_COLUMN = 4
RULE_COLUMN = 1
SCOPE_COLUMN = 3


def report_rows(workbook: Path) -> list[tuple[str, str, str]]:
    """(rule, scope, status) for every row of the report sheet."""
    ws = load_workbook(workbook, data_only=True).worksheets[0]
    return [
        (ws.cell(r, RULE_COLUMN).value,
         ws.cell(r, SCOPE_COLUMN).value,
         ws.cell(r, STATUS_COLUMN).value)
        for r in range(FIRST_DATA_ROW, ws.max_row + 1)
    ]


_CACHE: dict[str, tuple[Path, int]] = {}


@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("validated")


def validate(source: Path, workdir: Path) -> tuple[Path, int]:
    """Validate once per sample and reuse the result.

    Recalculation through LibreOffice dominates the runtime, so it is skipped
    here — these tests read the result table, not the summary formula, and
    `test_validate.py` covers the recalculated cell separately. Caching on top
    keeps a 24-workbook suite to a couple of seconds; a slow suite is one
    people stop running.
    """
    if source.name not in _CACHE:
        out = workdir / f"{source.stem}_validation.xlsx"
        _CACHE[source.name] = (out, main([str(source), "--out", str(out), "--no-recalc"]))
    return _CACHE[source.name]


def failing_rules(workbook: Path) -> set[str]:
    return {rule for rule, _, status in report_rows(workbook) if status == "FAIL"}


def test_the_clean_sample_passes_every_check(workdir):
    out, code = validate(CLEAN, workdir)
    assert code == 0
    assert failing_rules(out) == set()
    ws = load_workbook(out, data_only=True).worksheets[0]
    assert ws["B6"].value.startswith("PASS")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.rule)
def test_each_rule_fails_on_its_own_sample(fixture, workdir):
    source = BAD_DIR / fixture.filename
    assert source.exists(), f"missing sample {source} — run tests/make_fixtures.py"

    out, code = validate(source, workdir)
    assert code == 1, f"{fixture.filename} should have failed validation"
    assert failing_rules(out) == {fixture.rule}, (
        f"{fixture.filename} ({fixture.note}) should trip only {fixture.rule}"
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.rule)
def test_the_report_names_where_the_problem_is(fixture, workdir):
    """A status of FAIL is not enough — the analyst has to know where to look."""
    out, _ = validate(BAD_DIR / fixture.filename, workdir)
    ws = load_workbook(out, data_only=True).worksheets[0]
    details = [
        ws.cell(r, 5).value
        for r in range(FIRST_DATA_ROW, ws.max_row + 1)
        if ws.cell(r, STATUS_COLUMN).value == "FAIL"
    ]
    assert details and all(d for d in details), "a FAIL row with no detail is useless"


def test_every_rule_has_a_sample():
    """The guard that keeps this honest as rules are added.

    A rule with no sample has never been shown to detect anything.
    """
    uncovered = {rule.id for rule in RULES} - {f.rule for f in FIXTURES}
    assert not uncovered, f"rules with no sample workbook: {sorted(uncovered)}"


def test_samples_all_reference_a_real_rule():
    unknown = {f.rule for f in FIXTURES} - {rule.id for rule in RULES}
    assert not unknown, f"samples referencing rules that no longer exist: {sorted(unknown)}"
