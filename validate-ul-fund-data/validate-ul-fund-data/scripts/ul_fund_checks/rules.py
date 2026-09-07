"""The format contract for UL_FUND_DATA workbooks.

**This is the file you edit when the dataset changes.** It is meant to be read
top to bottom as a specification: one line per rule, in the same order as the
documented test table. Nothing here loops or branches — the machinery lives in
``checks.py`` and ``runner.py``.

To add a rule: append a ``Rule`` here, then add a fixture in
``scripts/make_fixtures.py`` that violates it. ``tests/test_rules.py`` fails if
a rule has no fixture, so an unexercised rule cannot quietly ship.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .checks import (
    Check,
    age_at_most,
    consistent_number_format,
    date_order,
    date_within,
    exact_sheet_names,
    identical_headers,
    matches_sheet_name,
    no_blank_rows,
    no_formulas,
    no_merged_or_hidden,
    no_unexpected_columns,
    numeric,
    one_of,
    real_date,
    required_columns,
    single_distinct_value,
    unique_within_sheet,
    whole_number,
)


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    check: Check


# --- Things the contract depends on ---------------------------------------

SHEETS = ("FUND_1", "FUND_2", "FUND_3")

COLUMNS = (
    "DOWNLOAD_TIME",
    "START_DATE",
    "FUND_NAME",
    "POLICY_ID",
    "STATUS",
    "GENDER",
    "BIRTH_DATE",
    "NUM_UNITS",
    "PRICE_UNIT",
    "ANNUAL_PREMIUM",
)

DATE_COLUMNS = ("DOWNLOAD_TIME", "START_DATE", "BIRTH_DATE")

EARLIEST_BIRTH_DATE = dt.date(1900, 1, 1)
LATEST_BIRTH_DATE = dt.date.today()
MAX_AGE_AT_START = 100


# --- The rules ------------------------------------------------------------

RULES: list[Rule] = [
    # Workbook and sheet structure
    Rule("W1", "Sheets are exactly FUND_1, FUND_2, FUND_3",
         exact_sheet_names(*SHEETS)),
    Rule("W2", "Every sheet carries the same headers in the same order",
         identical_headers()),
    Rule("W3", "No blank rows inside the data range",
         no_blank_rows()),
    Rule("W4", "No merged cells and no hidden sheets",
         no_merged_or_hidden()),
    Rule("C1", "All ten expected columns are present",
         required_columns(*COLUMNS)),
    Rule("C2", "No unexpected extra columns",
         no_unexpected_columns(*COLUMNS)),

    # Storage and formatting
    Rule("F1", "No formulas anywhere in the data range",
         no_formulas()),
    Rule("F2", "Date columns hold real dates, not text",
         real_date(*DATE_COLUMNS)),
    Rule("F3", "Date columns use one number format workbook-wide",
         consistent_number_format(*DATE_COLUMNS)),

    # Column values
    Rule("D1", "DOWNLOAD_TIME has one distinct value workbook-wide",
         single_distinct_value("DOWNLOAD_TIME")),
    Rule("D3", "FUND_NAME equals the name of its sheet",
         matches_sheet_name("FUND_NAME")),
    Rule("D4", "POLICY_ID is a whole number greater than 0",
         whole_number("POLICY_ID", minimum=1)),
    Rule("D5", "POLICY_ID is unique within each sheet (repeats across sheets are allowed)",
         unique_within_sheet("POLICY_ID")),
    Rule("D6", "STATUS is ACTIVE or PASSIVE",
         one_of("STATUS", {"ACTIVE", "PASSIVE"})),
    Rule("D7", "GENDER is M or F",
         one_of("GENDER", {"M", "F"})),
    Rule("D8", "BIRTH_DATE falls in a plausible range",
         date_within("BIRTH_DATE", EARLIEST_BIRTH_DATE, LATEST_BIRTH_DATE,
                     described_as=f"{EARLIEST_BIRTH_DATE}..today")),
    Rule("D9", "NUM_UNITS is numeric and at least 0",
         numeric("NUM_UNITS", minimum=0)),
    Rule("D10", "PRICE_UNIT is numeric and greater than 0",
         numeric("PRICE_UNIT", minimum=0, exclusive=True)),
    Rule("D11", "ANNUAL_PREMIUM is numeric and at least 0 (zero is valid for any STATUS)",
         numeric("ANNUAL_PREMIUM", minimum=0)),

    # Row-spanning
    Rule("X1", "BIRTH_DATE precedes START_DATE on the same row",
         date_order("BIRTH_DATE", "START_DATE")),
    Rule("X2", "START_DATE is on or before DOWNLOAD_TIME",
         date_order("START_DATE", "DOWNLOAD_TIME", allow_equal=True)),
    Rule("X3", f"Implied age at START_DATE is at most {MAX_AGE_AT_START}",
         age_at_most("BIRTH_DATE", "START_DATE", MAX_AGE_AT_START)),
]
