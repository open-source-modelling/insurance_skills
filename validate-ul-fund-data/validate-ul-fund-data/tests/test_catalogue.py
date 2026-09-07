"""Keeps ``examples/WHAT_EACH_RULE_CATCHES.md`` honest.

A committed document showing what the skill reports is only useful while it is
true. A stale one is worse than none: it advertises behaviour the code no
longer has, to exactly the people relying on it for reassurance.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from show_failures import CATALOGUE, build  # noqa: E402

from ul_fund_checks import RULES  # noqa: E402


def test_catalogue_is_up_to_date():
    assert CATALOGUE.exists(), "run: python3 tests/show_failures.py"
    with tempfile.TemporaryDirectory() as tmp:
        current = build(Path(tmp))
    assert CATALOGUE.read_text(encoding="utf-8") == current, (
        "examples/WHAT_EACH_RULE_CATCHES.md is stale — run tests/show_failures.py"
    )


def test_catalogue_covers_every_rule():
    text = CATALOGUE.read_text(encoding="utf-8")
    missing = [rule.id for rule in RULES if f"### {rule.id} — " not in text]
    assert not missing, f"rules absent from the catalogue: {missing}"


def test_catalogue_records_no_stray_failures():
    assert "which it should not" not in CATALOGUE.read_text(encoding="utf-8")
