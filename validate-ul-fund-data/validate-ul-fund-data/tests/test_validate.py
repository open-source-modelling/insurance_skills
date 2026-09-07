"""Tests for the report-writing layer.

The 22 rules themselves are tested in the ul-fund-format-hook repository; these
cover only what this skill adds — that the report sheet is written correctly,
in front, into a copy, and survives a workbook too broken to name its quarter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate import main  # noqa: E402

EXAMPLES = ROOT / "examples"
CLEAN = EXAMPLES / "good" / "UL_FUND_DATA_Q4_2025.xlsx"
BROKEN = EXAMPLES / "bad" / "f2_text_date.xlsx"
NO_QUARTER = EXAMPLES / "real" / "UL_FUND_DATA_Q4_2025_as_supplied.xlsx"

pytestmark = pytest.mark.skipif(not CLEAN.exists(), reason="fixtures missing")


def run(src: Path, tmp_path: Path) -> Path:
    out = tmp_path / "validated.xlsx"
    return out, main([str(src), "--out", str(out)])


def test_clean_workbook_passes_and_reports_pass(tmp_path):
    out, code = run(CLEAN, tmp_path)
    assert code == 0
    ws = load_workbook(out, data_only=True).worksheets[0]
    assert ws.title == "Validation Q4 2025"
    assert ws["B6"].value.startswith("PASS")


def test_report_is_standalone_and_carries_no_dataset(tmp_path):
    """The report must not become a second copy of the quarterly data.

    A workbook holding both the report and the fund sheets leaves nobody sure
    which of the two files is the real extract.
    """
    out, _ = run(CLEAN, tmp_path)
    assert load_workbook(out).sheetnames == ["Validation Q4 2025"]


def test_report_fingerprints_the_source_it_describes(tmp_path):
    """A standalone report proves nothing about which extract it validated."""
    out, _ = run(CLEAN, tmp_path)
    ws = load_workbook(out, data_only=True).worksheets[0]
    assert ws["B2"].value == CLEAN.name
    assert len(str(ws["B3"].value).split()[0]) == 16


def test_broken_workbook_exits_1_and_names_the_rule(tmp_path):
    out, code = run(BROKEN, tmp_path)
    assert code == 1
    ws = load_workbook(out, data_only=True).worksheets[0]
    assert ws["B6"].value.startswith("FAIL")
    # F2 covers three date columns on three sheets, so it produces several rows.
    # Exactly one of them — FUND_1!START_DATE — should be the failure.
    f2 = [
        (ws.cell(r, 3).value, ws.cell(r, 4).value)
        for r in range(9, ws.max_row + 1)
        if ws.cell(r, 1).value == "F2"
    ]
    failures = [scope for scope, status in f2 if status == "FAIL"]
    assert failures == ["FUND_1!START_DATE"]


def test_summary_cell_has_a_value_not_an_empty_formula(tmp_path):
    out, _ = run(CLEAN, tmp_path)
    ws = load_workbook(out, data_only=True).worksheets[0]
    assert ws["B7"].value and "checks passed" in str(ws["B7"].value)


def test_uploaded_file_is_left_untouched(tmp_path):
    before = CLEAN.read_bytes()
    run(CLEAN, tmp_path)
    assert CLEAN.read_bytes() == before


def test_workbook_with_unreadable_download_time_still_gets_a_report(tmp_path):
    """The files most in need of a report are the ones too broken to name."""
    out, code = run(NO_QUARTER, tmp_path)
    assert code == 1
    assert load_workbook(out).worksheets[0].title == "Validation"
