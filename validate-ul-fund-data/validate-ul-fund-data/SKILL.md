---
name: validate-ul-fund-data
description: Validate quarterly UL fund Excel extracts before analysis. Use whenever a quarterly extract is uploaded, mentioned, or suspected to be malformed, including requests to check, QA, sanity-check, or verify it. Run before analysis even when validation is not explicitly requested.
---

# Validate UL fund data

This runs 22 format checks on quarterly fund extracts and produces an
audit-friendly report before analysis.

## Why a separate workbook

Write a ***standalone report workbook*** `<source>_validation.xlsx` report containing only one sheet,
`Validation Q<n> <year>`. Never modify or copy the source workbook. Include the
source filename, SHA-256 fingerprint, and byte count so the report remains tied
to the file checked.

## Workflow

Find the uploaded workbook in `/mnt/user-data/uploads/` by listing the
directory, then run:

```bash
python3 scripts/validate.py /mnt/user-data/uploads/<file>.xlsx
```

It writes `/mnt/user-data/outputs/<file>_validation.xlsx` and prints a summary.
Exit code is `0` if every check passed, `1` if any failed. Present the report
with `present_files`, and tell the analyst to save it beside the dataset. In
chat, lead with PASS/FAIL and list each failed rule with its sheet, column,
reason, and cell references.

## When checks fail

Say plainly that a failed extract should not be analysed yet. List the failed
rule, sheet, column, reason, and actual cell references such as `FUND_1!B2`.
Do not provide partial totals or estimates from invalid data. If the analyst
overrides this, state the unreliable findings and note them beside any result.

## What the checks cover

22 rules across sheet structure, column presence, storage types, value ranges
and row-level consistency. `references/rules.md` lists all of them, along with
what is deliberately *not* enforced — read it when a failure message alone is
not enough to explain what went wrong.

The three that fire most often on real exports:

- **F2** — `DOWNLOAD_TIME` arrives as the text `31/12/2025` rather than a date.
- **F1** — `BIRTH_DATE` written as `=DATE(y,m,d)` formulas rather than values.
- **C1 / C2** — a column renamed upstream without the contract being updated.

## Showing the analyst it works

`examples/WHAT_EACH_RULE_CATCHES.md` maps every rule to a one-defect workbook
and expected finding. Use it when someone wants evidence that the skill works.
The examples in `examples/bad/` each fail one rule; `examples/good/` passes all
checks. To verify the skill:

```bash
python3 -m pytest tests/ -q                            # 57 checks
python3 -m pytest tests/test_every_rule_fails.py -v    # one line per rule
```

The suite asserts that each sample trips its own rule **and no others**, and
fails if any rule has no fixture.

## When the dataset changes

The rules live in `scripts/ul_fund_checks/rules.py` as an ordered specification.
To add one:

1. a small function in `scripts/ul_fund_checks/checks.py`;
2. a line in `rules.py`;
3. a sample workbook in `tests/make_fixtures.py` that violates it.

Regenerate fixtures and run the tests:

```bash
python3 tests/make_fixtures.py     # the sample workbooks
python3 tests/show_failures.py     # the catalogue
python3 -m pytest tests/ -q
```

This is the same validator used by the pre-commit hook in the
`ul-fund-format-hook` repository. Keep both copies synchronized when changing
a rule.
