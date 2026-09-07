# Validate files before analysis

A Claude skill for checking structured files before anyone analyses them or
passes them to another process. The included contract validates quarterly
unit-linked fund Excel extracts, but the skill is intended to be adapted to
your own file format and quality rules.

## Install the skill

Use the packaged file, not the source folder:

```text
validate-ul-fund-data.skill
```

In Claude, open the Skills manager, choose **Add** or **Upload skill**, and
select the `.skill` file. Enable the skill for the conversation or project
where the files will be checked. The package contains `SKILL.md`, the
validator scripts, and the report-writing code; users do not need to copy the
tests or install the development source tree.

After installation, upload a workbook and ask Claude to validate it. The skill
also activates for requests such as checking whether a quarterly extract is
ready, sanity-checking an export, or verifying a file before analysis.

## Use it

The normal workflow is:

1. Upload the file to Claude.
2. Ask Claude to validate or check the file.
3. Review the verdict and any failed rules in the response.
4. Open the generated validation workbook and send it to the file producer if corrections are needed.

The skill runs every configured check, rather than stopping at the first failure. The report identifies the rule, sheet or column, reason, and example
cell references. The original workbook is left untouched, and the report is a
standalone workbook with a source filename, SHA-256 fingerprint, status, and
check results.

The included example has 22 rules covering workbook structure, expected
columns, storage types, value ranges, and row-level consistency. See
`references/rules.md` for the current contract.

## Adapt it for your files

The UL fund schema is a starting point, not a requirement. To make this skill
validate another spreadsheet or export:

1. Change `SKILL.md` so Claude knows which files to recognize, when to run the
   skill, and how to present the result.
2. Replace the schema constants and ordered `RULES` list in
   `scripts/ul_fund_checks/rules.py`.
3. Reuse the factories in `checks.py` for required columns, allowed values,
   numeric ranges, dates, uniqueness, sheet structure, and row relationships.
4. Add a focused check to `checks.py` when a rule needs custom domain logic or
   compares several rows, columns, or sheets.
5. Update `reading.py` if the input is not an Excel workbook. Its job is to
   convert the source into the `Book`, `Sheet`, `Column`, and `Cell` model used
   by the checks.
6. Update `validate.py` if the output should be a different report format.

Give each rule a stable identifier. Rule IDs appear in Claude's summary, the
report workbook, JSON output, and any automation built around the validator.
Keep each finding specific: say what failed, where it failed, and include a
small number of real references or values.

## Optional automation

The same validator can run outside Claude as a manual command, pre-commit
hook, or CI step:

The Bash examples below assume they are run from this repository's top-level
directory. On Windows, use `py -3` wherever the examples say `python3`.

```bash
cd validate-ul-fund-data-source/validate-ul-fund-data
python3 -m scripts.ul_fund_checks.cli --json export.xlsx
```

Exit code `0` means the file passed. A non-zero exit code means one or more
rules failed. Use `scripts/validate.py` when an analyst-facing Excel report is
needed:

```bash
cd validate-ul-fund-data-source/validate-ul-fund-data
python3 scripts/validate.py export.xlsx --out export_validation.xlsx
```

## Occasional maintainer checks

Tests are for maintaining or extending the skill, not for normal installation
or day-to-day use. When changing the contract, add a fixture that violates the
new rule and run:

```bash
cd validate-ul-fund-data-source/validate-ul-fund-data
python3 tests/make_fixtures.py
python3 tests/show_failures.py
python3 -m pytest tests/ -q
```

The examples in `examples/` provide one passing workbook and one deliberately
broken workbook per rule. `examples/WHAT_EACH_RULE_CATCHES.md` records the
expected finding for each sample.