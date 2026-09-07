"""The check library.

**This is the file you extend.** Every check is a factory returning a callable
with one signature::

    check(book: Book, rule_id: str) -> Iterable[Finding]

Most new rules do not need a new function at all — they need one more entry in
``rules.py`` using ``column_predicate`` or one of the wrappers below. Reach for
a hand-written check only when a rule spans rows or sheets.

Three conventions worth keeping when you add one:

* A missing *optional* column yields N/A, never FAIL.
* Blank is its own failure reason, distinct from "wrong type".
* Every FAIL names real cell references, taken from the *failing* rows.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Iterable

from .findings import Finding, failed, not_applicable, passed
from .reading import Book, Cell, Column, Sheet

Check = Callable[[Book, str], Iterable[Finding]]


# --------------------------------------------------------------------------
# Adapters — turn a small per-sheet or per-column function into a Check
# --------------------------------------------------------------------------


def each_sheet(fn: Callable[[Sheet, str], Iterable[Finding]]) -> Check:
    """Run ``fn`` once per sheet."""

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        for sheet in book:
            yield from fn(sheet, rule_id)

    return check


def each_column(column_name: str, fn: Callable[[Column, str], Iterable[Finding]]) -> Check:
    """Run ``fn`` on one named column of every sheet.

    A sheet without the column yields N/A. Column presence itself is C1's job,
    so it is not re-reported as a failure here.
    """

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        for sheet in book:
            column = sheet.column(column_name)
            if column is None:
                yield not_applicable(
                    rule_id, "column not present on this sheet",
                    sheet=sheet.name, column=column_name,
                )
                continue
            yield from fn(column, rule_id)

    return check


def _show(value: Any) -> str:
    """Compact rendering for failure examples.

    ``repr`` on a date gives ``datetime.datetime(1899, 6, 1, 0, 0)``, which is
    noise in a report someone has to scan.
    """
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return repr(value)


def column_predicate(
    column_name: str,
    ok: Callable[[Any], bool],
    message: str,
    *,
    blanks_ok: bool = False,
) -> Check:
    """The workhorse: every cell in ``column_name`` must satisfy ``ok``.

    ``ok`` never sees a blank — blanks are reported separately, because "blank"
    and "wrong type" almost always have different causes and different fixes.
    """

    def run(column: Column, rule_id: str) -> Iterable[Finding]:
        scope = {"sheet": column.sheet, "column": column.name}
        blanks = column.where(lambda c: c.is_blank)
        if blanks and not blanks_ok:
            yield failed(
                rule_id, f"{len(blanks)} blank cell(s)",
                examples=[c.ref for c in blanks], **scope,
            )
            return
        bad = column.where(lambda c: not c.is_blank and not ok(c.value))
        if bad:
            yield failed(
                rule_id, f"{len(bad)} cell(s) {message}",
                examples=[f"{c.ref}={_show(c.value)}" for c in bad], **scope,
            )
        else:
            yield passed(rule_id, f"all {len(column.cells)} values ok", **scope)

    return each_column(column_name, run)


# --------------------------------------------------------------------------
# Value predicates — small, reusable, easy to read at the call site
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    # bool is a subclass of int in Python; TRUE in a numeric column is an error.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_date(value: Any) -> bool:
    return isinstance(value, (dt.datetime, dt.date))


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    return value if isinstance(value, dt.date) else None


# --------------------------------------------------------------------------
# Column checks
# --------------------------------------------------------------------------


def real_date(*column_names: str) -> Check:
    """Stored as genuine Excel dates. Text that merely looks like one fails."""
    parts = [
        column_predicate(name, _is_date, "are not stored as real dates (text or numeric)")
        for name in column_names
    ]

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        for part in parts:
            yield from part(book, rule_id)

    return check


def whole_number(column_name: str, *, minimum: int | None = None) -> Check:
    def ok(value: Any) -> bool:
        if not _is_number(value) or float(value) != int(value):
            return False
        return minimum is None or value >= minimum

    tail = "" if minimum is None else f" >= {minimum}"
    return column_predicate(column_name, ok, f"are not whole numbers{tail}")


def numeric(column_name: str, *, minimum: float | None = None, exclusive: bool = False) -> Check:
    def ok(value: Any) -> bool:
        if not _is_number(value):
            return False
        if minimum is None:
            return True
        return value > minimum if exclusive else value >= minimum

    if minimum is None:
        tail = ""
    else:
        tail = f" {'>' if exclusive else '>='} {minimum}"
    return column_predicate(column_name, ok, f"are not numeric{tail}")


def one_of(column_name: str, allowed: set[str]) -> Check:
    return column_predicate(
        column_name,
        lambda v: v in allowed,
        f"are outside {sorted(allowed)}",
    )


def date_within(
    column_name: str,
    earliest: dt.date,
    latest: dt.date,
    *,
    described_as: str | None = None,
) -> Check:
    """Dates must fall in ``earliest..latest``.

    ``described_as`` keeps the failure message stable when a bound is computed
    at runtime — embedding ``date.today()`` would make every run's output
    differ, which breaks the committed failure catalogue.
    """
    def ok(value: Any) -> bool:
        date = _as_date(value)
        return date is not None and earliest <= date <= latest

    bounds = described_as or f"{earliest}..{latest}"
    return column_predicate(column_name, ok, f"fall outside {bounds}")


def matches_sheet_name(column_name: str) -> Check:
    """Every value equals the name of the sheet it sits on."""

    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        column = sheet.column(column_name)
        scope = {"sheet": sheet.name, "column": column_name}
        if column is None:
            yield not_applicable(rule_id, "column not present on this sheet", **scope)
            return
        bad = column.where(lambda c: c.value != sheet.name)
        if bad:
            yield failed(
                rule_id, f"{len(bad)} value(s) do not equal the sheet name",
                examples=[f"{c.ref}={_show(c.value)}" for c in bad], **scope,
            )
        else:
            yield passed(rule_id, f"all values equal {sheet.name!r}", **scope)

    return each_sheet(run)


def unique_within_sheet(column_name: str) -> Check:
    """Duplicates within one sheet fail. Repeats ACROSS sheets are allowed.

    That is deliberate for POLICY_ID: one policy can hold units in more than
    one fund, so the same id legitimately appears on several sheets.
    """

    def run(column: Column, rule_id: str) -> Iterable[Finding]:
        scope = {"sheet": column.sheet, "column": column.name}
        seen: dict[Any, Cell] = {}
        dupes: list[Cell] = []
        for cell in column:
            if cell.value in seen:
                dupes.append(cell)
            else:
                seen[cell.value] = cell
        if dupes:
            yield failed(
                rule_id, f"{len(dupes)} duplicate value(s) within this sheet",
                examples=[f"{c.ref}={_show(c.value)}" for c in dupes], **scope,
            )
        else:
            yield passed(rule_id, f"{len(seen)} distinct values", **scope)

    return each_column(column_name, run)


def single_distinct_value(column_name: str) -> Check:
    """One value across the whole workbook — e.g. the extract timestamp."""

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        by_value: dict[Any, list[Cell]] = {}
        for sheet in book:
            column = sheet.column(column_name)
            if column is None:
                continue
            for cell in column:
                by_value.setdefault(cell.value, []).append(cell)
        if not by_value:
            yield not_applicable(rule_id, "column not present anywhere", column=column_name)
        elif len(by_value) == 1:
            yield passed(
                rule_id, f"one value workbook-wide: {_show(next(iter(by_value)))}", column=column_name
            )
        else:
            yield failed(
                rule_id, f"{len(by_value)} distinct values workbook-wide",
                examples=[f"{cells[0].ref}={_show(v)}" for v, cells in by_value.items()],
                column=column_name,
            )

    return check


# --------------------------------------------------------------------------
# Row-spanning checks
# --------------------------------------------------------------------------


def date_order(earlier: str, later: str, *, allow_equal: bool = False) -> Check:
    """``earlier`` must precede ``later`` on the same row."""

    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        scope = {"sheet": sheet.name, "column": f"{earlier} vs {later}"}
        if not {earlier, later} <= set(sheet.columns):
            yield not_applicable(rule_id, "one or both columns absent", **scope)
            return
        bad = []
        for row in sheet.rows():
            a, b = _as_date(row[earlier].value), _as_date(row[later].value)
            if a is None or b is None:
                continue  # a type problem, reported by the date checks
            if (a > b) if allow_equal else (a >= b):
                bad.append(row[earlier])
        if bad:
            relation = "after" if allow_equal else "on or after"
            yield failed(
                rule_id, f"{len(bad)} row(s) where {earlier} is {relation} {later}",
                examples=[c.ref for c in bad], **scope,
            )
        else:
            yield passed(rule_id, f"{earlier} precedes {later} on every row", **scope)

    return each_sheet(run)


def age_at_most(birth: str, at: str, max_years: int) -> Check:
    """Implied age on the ``at`` date must not exceed ``max_years``."""

    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        scope = {"sheet": sheet.name, "column": f"age at {at}"}
        if not {birth, at} <= set(sheet.columns):
            yield not_applicable(rule_id, "one or both columns absent", **scope)
            return
        bad = []
        for row in sheet.rows():
            b, a = _as_date(row[birth].value), _as_date(row[at].value)
            if b is None or a is None:
                continue
            years = (a - b).days / 365.25
            if years > max_years:
                bad.append((row[birth], years))
        if bad:
            yield failed(
                rule_id, f"{len(bad)} row(s) imply an age above {max_years}",
                examples=[f"{c.ref}={y:.0f}y" for c, y in bad], **scope,
            )
        else:
            yield passed(rule_id, f"every implied age is at most {max_years}", **scope)

    return each_sheet(run)


# --------------------------------------------------------------------------
# Workbook and sheet structure
# --------------------------------------------------------------------------


def exact_sheet_names(*expected: str) -> Check:
    """Exactly these sheets, no more and no fewer."""

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        actual, wanted = set(book.sheet_names), set(expected)
        missing, extra = sorted(wanted - actual), sorted(actual - wanted)
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing {missing}")
            if extra:
                parts.append(f"unexpected {extra}")
            yield failed(rule_id, "; ".join(parts))
        else:
            yield passed(rule_id, f"exactly {sorted(wanted)}")

    return check


def identical_headers() -> Check:
    """Every sheet carries the same headers in the same order."""

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        layouts: dict[tuple[str, ...], list[str]] = {}
        for sheet in book:
            layouts.setdefault(sheet.headers, []).append(sheet.name)
        if len(layouts) <= 1:
            yield passed(rule_id, "all sheets share one header layout")
        else:
            groups = "; ".join(
                f"{names}: {list(headers)}" for headers, names in layouts.items()
            )
            yield failed(rule_id, f"{len(layouts)} different header layouts — {groups}")

    return check


def required_columns(*expected: str) -> Check:
    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        missing = [c for c in expected if c not in sheet.columns]
        if missing:
            yield failed(rule_id, f"missing column(s) {missing}", sheet=sheet.name)
        else:
            yield passed(rule_id, f"all {len(expected)} expected columns present", sheet=sheet.name)

    return each_sheet(run)


def no_unexpected_columns(*expected: str) -> Check:
    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        extra = [c for c in sheet.headers if c not in expected]
        if extra:
            yield failed(rule_id, f"unexpected column(s) {extra}", sheet=sheet.name)
        else:
            yield passed(rule_id, "no unexpected columns", sheet=sheet.name)

    return each_sheet(run)


def no_blank_rows() -> Check:
    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        if sheet.blank_rows:
            yield failed(
                rule_id, f"{len(sheet.blank_rows)} blank row(s) inside the data range",
                examples=[f"row {r}" for r in sheet.blank_rows], sheet=sheet.name,
            )
        else:
            yield passed(rule_id, f"{sheet.n_data_rows} contiguous data rows", sheet=sheet.name)

    return each_sheet(run)


def no_merged_or_hidden() -> Check:
    def run(sheet: Sheet, rule_id: str) -> Iterable[Finding]:
        problems = []
        if sheet.has_merged_cells:
            problems.append("merged cells")
        if sheet.is_hidden:
            problems.append("sheet is hidden")
        if problems:
            yield failed(rule_id, " and ".join(problems), sheet=sheet.name)
        else:
            yield passed(rule_id, "no merged cells, sheet visible", sheet=sheet.name)

    return each_sheet(run)


def no_formulas() -> Check:
    """A data extract should contain values, not formulas.

    A formula means the file was hand-edited rather than exported, and a
    non-Excel writer regenerating it leaves every such cell empty.
    """

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        for sheet in book:
            found = [c for col in sheet.columns.values() for c in col if c.is_formula]
            if found:
                yield failed(
                    rule_id, f"{len(found)} formula cell(s) in the data range",
                    examples=[c.ref for c in found], sheet=sheet.name,
                )
            else:
                yield passed(rule_id, "no formulas in the data range", sheet=sheet.name)

    return check


def consistent_number_format(*column_names: str) -> Check:
    """Each named column uses one number format across the whole workbook.

    Formats are genuinely not uniform in real exports — one sheet arriving as
    ``yyyy-mm-dd`` while the others are ``mm-dd-yy`` is how dd/mm vs mm/dd
    ambiguity gets into an analysis unnoticed.
    """

    def check(book: Book, rule_id: str) -> Iterable[Finding]:
        for name in column_names:
            formats: dict[str, Cell] = {}
            for sheet in book:
                column = sheet.column(name)
                if column is None:
                    continue
                for cell in column:
                    if cell.is_blank:
                        continue  # a blank cell's format tells us nothing
                    formats.setdefault(cell.number_format, cell)
            if not formats:
                yield not_applicable(rule_id, "column not present anywhere", column=name)
            elif len(formats) == 1:
                yield passed(rule_id, f"one format workbook-wide: {next(iter(formats))}", column=name)
            else:
                yield failed(
                    rule_id, f"{len(formats)} different number formats",
                    examples=[f"{c.ref}={fmt}" for fmt, c in formats.items()], column=name,
                )

    return check
