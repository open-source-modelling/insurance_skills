# The 22 format rules

Generated from `scripts/ul_fund_checks/rules.py`. Each rule has a workbook in the
source repository that breaks it and nothing else.

| Rule | Check |
| --- | --- |
| W1 | Sheets are exactly FUND_1, FUND_2, FUND_3 |
| W2 | Every sheet carries the same headers in the same order |
| W3 | No blank rows inside the data range |
| W4 | No merged cells and no hidden sheets |
| C1 | All ten expected columns are present |
| C2 | No unexpected extra columns |
| F1 | No formulas anywhere in the data range |
| F2 | Date columns hold real dates, not text |
| F3 | Date columns use one number format workbook-wide |
| D1 | DOWNLOAD_TIME has one distinct value workbook-wide |
| D3 | FUND_NAME equals the name of its sheet |
| D4 | POLICY_ID is a whole number greater than 0 |
| D5 | POLICY_ID is unique within each sheet (repeats across sheets are allowed) |
| D6 | STATUS is ACTIVE or PASSIVE |
| D7 | GENDER is M or F |
| D8 | BIRTH_DATE falls in a plausible range |
| D9 | NUM_UNITS is numeric and at least 0 |
| D10 | PRICE_UNIT is numeric and greater than 0 |
| D11 | ANNUAL_PREMIUM is numeric and at least 0 (zero is valid for any STATUS) |
| X1 | BIRTH_DATE precedes START_DATE on the same row |
| X2 | START_DATE is on or before DOWNLOAD_TIME |
| X3 | Implied age at START_DATE is at most 100 |

## Deliberately not enforced

- The same `POLICY_ID` on several sheets is fine — one policy can hold units in
  more than one fund. Only within-sheet duplicates fail (D5).
- `ANNUAL_PREMIUM = 0` is valid for any `STATUS`, and a `PASSIVE` policy may carry
  a positive premium. The two columns are independent.
- `NUM_UNITS = 0` is valid.

## Common failures on real exports

- **F2** — `DOWNLOAD_TIME` arrives as the text `31/12/2025` rather than a date.
- **F1** — `BIRTH_DATE` written as `=DATE(y,m,d)` formulas rather than values.
- **C1/C2** — a column renamed upstream without the contract being updated.
