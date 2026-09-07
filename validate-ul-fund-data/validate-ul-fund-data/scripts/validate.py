"""Validate an uploaded UL fund workbook and write the results back into it.

The deliverable is a workbook, not a chat message. An analyst needs something
they can email to whoever sent the file, attach to their working papers, and
point at in three months when someone asks why a quarter was restated. A chat
answer disappears.

The report goes into a *copy*: the uploaded file is left untouched, so the
original extract stays exactly as it arrived from the source system.

Usage::

    python3 scripts/validate.py /mnt/user-data/uploads/UL_FUND_DATA_Q1_2026.xlsx \
        --out /mnt/user-data/outputs/UL_FUND_DATA_Q1_2026_validated.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ul_fund_checks import Outcome, read_workbook, validate_book  # noqa: E402
from ul_fund_checks.rules import RULES  # noqa: E402

FONT = "Arial"
HEADER_FILL = PatternFill("solid", start_color="1F3864")
PASS_FILL = PatternFill("solid", start_color="C6EFCE")
FAIL_FILL = PatternFill("solid", start_color="FFC7CE")
NA_FILL = PatternFill("solid", start_color="EDEDED")

DESCRIPTIONS = {rule.id: rule.description for rule in RULES}
FIRST_DATA_ROW = 9


def report_sheet_name(book) -> str:
    """Name the sheet after the quarter the extract covers.

    DOWNLOAD_TIME may be missing or malformed — that is one of the things being
    checked — so fall back rather than crashing on the very files this exists
    to diagnose.
    """
    for sheet in book:
        column = sheet.column("DOWNLOAD_TIME")
        if column is None:
            continue
        for cell in column:
            value = cell.value
            if isinstance(value, (dt.datetime, dt.date)):
                return f"Validation Q{(value.month - 1) // 3 + 1} {value.year}"
    return "Validation"


def extract_date(book) -> str:
    for sheet in book:
        column = sheet.column("DOWNLOAD_TIME")
        if column is None:
            continue
        for cell in column:
            if isinstance(cell.value, (dt.datetime, dt.date)):
                return cell.value.strftime("%d/%m/%Y")
            if cell.value not in (None, ""):
                return f"{cell.value}  (not stored as a date)"
    return "not found"


def fingerprint(path: Path) -> str:
    """Short SHA-256 of the source file.

    The report is a standalone file, so nothing about it proves which extract
    it describes. Without a fingerprint, a report sitting beside a re-exported
    workbook silently claims to describe data it never saw.
    """
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{digest[:16]}  ({path.stat().st_size:,} bytes)"


def write_report(source: Path, destination: Path, book, report, recalc: bool = True) -> str:
    """Build a standalone validation workbook alongside the dataset.

    Deliberately not a copy of the extract with a sheet bolted on: that would
    produce a second workbook containing the data, and nobody would be sure
    afterwards which of the two was the real quarterly file.
    """
    from openpyxl import Workbook

    destination.parent.mkdir(parents=True, exist_ok=True)
    name = report_sheet_name(book)

    wb = Workbook()
    ws = wb.active
    ws.title = name

    counts = report.counts()
    ws["A1"] = f"UL fund data validation — {source.name}"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)

    ws["A2"], ws["B2"] = "Source file:", source.name
    ws["A3"], ws["B3"] = "Source fingerprint (SHA-256):", fingerprint(source)
    ws["A4"], ws["B4"] = "Run date:", dt.date.today()
    ws["B4"].number_format = "dd/mm/yyyy"
    ws["A5"], ws["B5"] = "Extract date (DOWNLOAD_TIME):", extract_date(book)
    ws["A6"] = "Result:"
    ws["B6"] = "PASS — safe to analyse" if report.ok else "FAIL — do not analyse"
    ws["B6"].font = Font(name=FONT, bold=True)
    ws["B6"].fill = PASS_FILL if report.ok else FAIL_FILL
    ws["A7"] = "Summary:"
    last = FIRST_DATA_ROW + len(report.findings) - 1
    ws["B7"] = (
        f'=COUNTIF(D{FIRST_DATA_ROW}:D{last},"PASS")&" of "&'
        f'COUNTA(D{FIRST_DATA_ROW}:D{last})&" checks passed, "&'
        f'COUNTIF(D{FIRST_DATA_ROW}:D{last},"FAIL")&" failed"'
    )
    for row in range(2, 8):
        ws.cell(row, 1).font = Font(name=FONT, bold=True)
        if row != 6:  # B6 carries its own bold styling
            ws.cell(row, 2).font = Font(name=FONT)

    headers = ["Rule", "Check", "Sheet / column", "Status", "Details"]
    for index, title in enumerate(headers, start=1):
        cell = ws.cell(FIRST_DATA_ROW - 1, index, title)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
    ws.freeze_panes = f"A{FIRST_DATA_ROW}"

    for offset, finding in enumerate(report.findings):
        row = FIRST_DATA_ROW + offset
        details = finding.message
        if finding.examples:
            details += "  (e.g. " + ", ".join(finding.examples) + ")"
        for index, value in enumerate(
            [finding.rule_id, DESCRIPTIONS.get(finding.rule_id, ""),
             finding.scope, finding.outcome.value, details], start=1
        ):
            cell = ws.cell(row, index, value)
            cell.font = Font(name=FONT)
            cell.alignment = Alignment(vertical="top", wrap_text=(index == 5))
        ws.cell(row, 4).alignment = Alignment(horizontal="center", vertical="top")

    status_range = f"D{FIRST_DATA_ROW}:D{last}"
    for token, fill, colour in (
        ("PASS", PASS_FILL, "006100"), ("FAIL", FAIL_FILL, "9C0006"), ("N/A", NA_FILL, "5A5A5A"),
    ):
        ws.conditional_formatting.add(status_range, CellIsRule(
            operator="equal", formula=[f'"{token}"'], fill=fill,
            font=Font(name=FONT, color=colour),
        ))

    for letter, width in zip("ABCDE", (30, 46, 30, 10, 90)):
        ws.column_dimensions[letter].width = width

    wb.save(destination)
    _recalculate(destination, counts, recalc)
    return name


def _recalculate(destination: Path, counts: dict[str, int], recalc: bool = True) -> None:
    """Bake a cached value into the summary formula.

    openpyxl writes formulas with no cached result, so B5 would read as empty
    in anything that trusts cached values — which is most previewers, and
    Excel until the file is opened and recalculated. If the recalc helper is
    not available, replace the formula with the equivalent static text: a
    correct sentence beats a formula that renders blank.
    """
    import subprocess

    helper = Path("/mnt/skills/public/xlsx/scripts/recalc.py")
    if recalc and helper.exists():
        result = subprocess.run(
            [sys.executable, str(helper), str(destination)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and '"status": "success"' in result.stdout:
            return

    wb = load_workbook(destination)
    total = counts["PASS"] + counts["FAIL"] + counts["N/A"]
    wb.worksheets[0]["B7"] = (
        f"{counts['PASS']} of {total} checks passed, {counts['FAIL']} failed"
    )
    wb.save(destination)


def summarise(report, sheet_name: str, destination: Path) -> str:
    counts = report.counts()
    lines = []
    if report.ok:
        lines.append(
            f"PASS — all {counts['PASS']} checks passed. The extract is safe to analyse."
        )
    else:
        by_rule: dict[str, list] = {}
        for finding in report.failures:
            by_rule.setdefault(finding.rule_id, []).append(finding)
        lines.append(
            f"FAIL — {len(by_rule)} rule(s) failed across {counts['FAIL']} findings. "
            "Do not analyse this extract until it is fixed."
        )
        lines.append("")
        for rule_id, findings in by_rule.items():
            lines.append(f"  {rule_id}  {DESCRIPTIONS.get(rule_id, '')}")
            for finding in findings:
                detail = f"    {finding.scope}: {finding.message}"
                if finding.examples:
                    detail += "  (e.g. " + ", ".join(finding.examples[:3]) + ")"
                lines.append(detail)
    lines.append("")
    lines.append(
        f"Full results are in {destination.name} (sheet '{sheet_name}'), "
        "to be saved next to the dataset."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Validate a UL fund workbook and write a report sheet into a copy of it.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--no-recalc", action="store_true",
        help="skip the LibreOffice recalculation; the summary cell gets static text instead",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"{args.path}: file not found", file=sys.stderr)
        return 1

    destination = args.out or Path("/mnt/user-data/outputs") / (
        f"{args.path.stem}_validation.xlsx"
    )

    book = read_workbook(args.path)
    report = validate_book(book)
    sheet_name = write_report(args.path, destination, book, report,
                              recalc=not args.no_recalc)

    print(summarise(report, sheet_name, destination))
    print(f"\nWrote: {destination}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
