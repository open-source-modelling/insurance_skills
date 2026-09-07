"""Applies every rule to a workbook.

Deliberately dull. All the interesting decisions live in ``rules.py``; all the
reusable logic lives in ``checks.py``. If you find yourself editing this file
to add a rule, the rule probably wants a new helper in ``checks.py`` instead.

Two behaviours worth stating explicitly:

* **Every rule runs, always.** A failing rule does not stop the run. Someone
  repairing an export needs the whole list, not the first thing that broke.
* **Findings come back in rule order**, then sheet order, then row order, so
  the output is stable enough to assert against in tests.
"""

from __future__ import annotations

from pathlib import Path

from .findings import Report, failed
from .reading import Book, read_workbook
from .rules import RULES, Rule


def validate_book(book: Book, rules: list[Rule] | None = None) -> Report:
    report = Report()
    for rule in rules if rules is not None else RULES:
        try:
            report.extend(rule.check(book, rule.id))
        except Exception as exc:  # a broken check must not hide the other rules
            report.add(failed(rule.id, f"check raised {type(exc).__name__}: {exc}"))
    return report


def validate_file(path: str | Path, rules: list[Rule] | None = None) -> Report:
    return validate_book(read_workbook(path), rules)
