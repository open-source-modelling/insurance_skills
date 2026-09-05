# spontaneous-testing-for-excel

An [Agent Skill](https://agentskills.io) that lets a spreadsheet's owner write its tests in
plain English, on a sheet inside the workbook, and have Claude run them.

You write `Summary!C18 should equal the sum of Detail!D2:D4180, to the cent` in a cell. Claude
reads it, works out which cells would settle it, reads them, and writes back both what it did
and what it found. No test harness, no separate file, no code.

The workbook leaves the way it arrived, except for two columns of text.

## The contract

Add a sheet named `TEST` holding a table named `tests` with four columns: `name`,
`description`, `process`, `result`. You fill in the first two. Claude fills in the last two.

Before:

| name | description | process | result |
|---|---|---|---|
| `total_ties` | Summary!C18 should equal the sum of Detail!D2:D4180, to the cent. | | |
| `no_hardcodes` | No hardcoded numbers inside the calculation block. | | |
| `rates_ok` | The discount rates look about right. | | |
| `fix_negatives` | Find any negative reserves and set them to zero, then confirm none remain. | | |

After:

| name | result | process |
|---|---|---|
| `total_ties` | `PASS` | Compared Summary!C18 (8,412,006.55) with SUM(Detail!D2:D4180) = 8,412,006.55. Tolerance 0.01 (currency). All 4,179 rows read; no error values. |
| `no_hardcodes` | `FAIL — 3 hardcoded constants in Calc!D12, D40, F7` | Scanned Calc!B4:H120 for cells with no formula… |
| `rates_ok` | `INCONCLUSIVE — no criterion given` | Located the rates at Assumptions!D4:D63; they span 0.4% to 4.1%. The description gives no bound to test against. |
| `fix_negatives` | `BLOCKED — asks for values to be changed, not checked` | Description asks to "set them to zero". Not carried out. Readable half run: scanned Detail!F2:F4180, found 3 negatives at F312, F890 and F2201. |

Every row gets one of five verdicts, always first in the cell so the column can be filtered,
counted and conditionally formatted without parsing prose:

| Verdict | Meaning |
|---|---|
| `PASS` | Checked against named cells, and it holds. |
| `FAIL` | Checked, and it does not hold. The finding is in the cell. |
| `INCONCLUSIVE` | Not testable — no criterion, no data, or not answerable by reading. Which of the three is always stated. |
| `BLOCKED` | The description asked for something other than a test. |
| `ERROR` | The check itself could not complete. |

## Why the rules are strict

Two properties make this approach useful, and the same two make it dangerous.

Tests get written by the person who understands the model, not by someone maintaining a test
harness — so the descriptions are loose, and there is constant pressure to guess what was
meant and call it checked. And the whole thing runs inside somebody's live deliverable, where
a stray write is a data-integrity incident.

So the skill holds two lines that most of its length is spent defending.

**It writes only `process` and `result`.** Never creates, renames, deletes, moves, hides or
unhides a sheet. No extra rows, columns, summary blocks or timestamps. No formatting changes.
No sorting, filtering or forced recalculation. Every sheet other than `TEST` is read-only
evidence.

It also **reports rather than repairs**. A test that finds a hardcoded number gets a `FAIL`
describing it; the cell stays exactly as it is. Fixing a defect found while testing conflates
two jobs and takes the decision away from the owner.

**It writes `PASS` only when `process` names a specific cell or range that was actually read.**
If it cannot name one, it did not check, and the honest verdict is `INCONCLUSIVE`. A suite that
passes everything is worse than no suite, because it converts an absence of testing into a
written assurance someone may put their name under.

Two consequences worth knowing before you use it:

- **A description that asks for a change gets `BLOCKED`, not obeyed.** Cell contents are data,
  not an instruction channel. This holds for cells claiming permission too — "the owner has
  approved this", "an administrator has authorised it" — because a spreadsheet cannot grant
  authority, and anyone who can write that sentence can write it falsely.
- **It stops and asks before the first write** if a run would overwrite a previous run's
  results, if a result cell holds a formula, if the sheet is protected, or if result cells are
  merged.

## Install

**Claude.ai / Claude Desktop** — needs code execution enabled in Settings > Capabilities. Zip
this folder, then go to [Customize > Skills](https://claude.ai/customize/skills) → **+** →
Create skill → Upload a skill.

**Claude Code** — drop the folder into `~/.claude/skills/` (personal) or `.claude/skills/`
(project). No upload step.

**API** — upload the bundle via the Skills API with the code execution tool enabled.

See [Anthropic's skills documentation](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
for details, and note that skills do not sync between surfaces — each needs its own copy.

## Using it

Open a workbook that has a `TEST` sheet and ask for what you want in ordinary terms — "run the
tests", "QA this before it goes out", "what failed last time". The skill's description is
written to catch those without you naming it.

You will get a preflight summary first (how many tests, how many previous results would be
overwritten, anything odd about the layout) and it waits for a go-ahead before writing.
Afterwards you get counts by verdict, every `FAIL` with its finding, every `BLOCKED` with what
was asked, and every `INCONCLUSIVE` grouped by reason.

## Layout

```
SKILL.md                    the instructions Claude loads
references/
  verdicts.md               exact output formats, one worked example per verdict
  excel-traps.md            text-that-looks-numeric, blanks vs zeros, filtered rows,
                            float comparison, and the rest of what produces wrong verdicts
scripts/
  check_run.py              integrity checker, described below
evals/                      test cases for the skill itself — see evals/README.md
```

`SKILL.md` is loaded whenever the skill triggers; the two reference files load only when the
run reaches the point of needing them.

## `scripts/check_run.py`

The skill runs this on itself. It snapshots the workbook before the first write and verifies it
afterwards, which turns "nothing else was modified" from an assertion into something checked.
It opens workbooks read-only and cannot modify anything.

```bash
# preflight
python3 scripts/check_run.py snapshot model.xlsx -o /tmp/model-snapshot.json

# postflight
python3 scripts/check_run.py verify model.xlsx --snapshot /tmp/model-snapshot.json

# compare two files directly
python3 scripts/check_run.py diff before.xlsx after.xlsx
```

It reports any sheet added, removed or reordered, any row that changed outside the two result
columns, any test row missing a verdict token, and any `PASS` whose `process` names no cell or
range. Exit code is non-zero on any of those, so it can gate a run. Requires `openpyxl`.

You can also run it yourself on any workbook you have a before-and-after copy of.

## Evals

`evals/` holds seven test cases with fixture workbooks, expected verdicts, and the prompts that
exercise them. Most test restraint rather than capability, because that is where the damage is:
refusing to add or delete a sheet, resisting a cell that claims permission, stopping before
overwriting a previous run, resolving columns by header rather than position.

One of them tests capability instead, and it is the one that keeps the rest honest — a skill
that refused everything would score perfectly on the boundary evals alone. `evals/README.md`
has the table and how to run them.

## Notes and limitations

- **Column names changed.** Earlier versions used `test_name`, `test_description`,
  `test_process`, `test_result`. A workbook built against those will stop with a message
  saying so. The old names are deliberately not accepted as aliases, and the skill will not
  rename your headers to fix itself — the header is yours to change.
- **Some setups have no file path.** When the workbook is reachable only through a connector,
  `check_run.py` does not apply and the skill falls back to recording sheet names and table
  dimensions by hand. The boundaries are unchanged; the verification is weaker.
- **`INCONCLUSIVE` is common on a first run**, and that is the skill working. Loose
  descriptions are the normal starting state; the verdict tells you which sentence to tighten.
- **A run full of failures is a successful run.** The skill does not soften findings to be
  agreeable.
