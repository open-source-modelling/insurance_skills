# Historic EIOPA Yield Curve Skill

A self-contained [Claude Skill](https://www.anthropic.com/news/skills) for answering questions about historic EIOPA risk-free rate (RFR) yield curves — spot rates, full term structures, tenors over time, country comparisons, implied forwards, Solvency II stressed curves, and curve parameters (UFR, LLP, alpha, CRA, VA).

No network access, database, or third-party packages required. Every monthly EIOPA release is bundled directly into this repo as a compact binary pack (~370 KB), so lookups run offline in well under a second.

## What's in here

```
historic-eiopa-yield-curve/
├── SKILL.md                    # Skill instructions Claude reads to know how to use this
├── references/
│   └── dataset.md              # Currency/country label table, pack format, shock formulas
└── scripts/
    ├── eiopa_curves.py         # The lookup tool — reads the pack and nothing else
    ├── data/
    │   └── curves.pack         # Bundled snapshot: every EIOPA release, all curves, terms 1–150y
    └── tests/
        └── test_eiopa_curves.py
```

## Coverage

- 140 monthly releases, 2014-12-31 through 2026-07-31 (as of this snapshot)
- 53 distinct curves, terms 1–150 years, whole-year nodes
- Both `no_VA` and `with_VA` curve types
- 54 countries/currencies, including all EEA members plus a set of non-EEA curves (dropped from EIOPA's publication starting 2025-01-31 — see `coverage --country XX` for per-country availability)

**This is a snapshot, not a live feed.** It cannot know about EIOPA releases published after the snapshot date. Run `coverage` or `facts` (see below) for the exact cutoff before relying on "latest."

## Installing

1. Clone this repo, or download it as a zip.
2. Copy the `historic-eiopa-yield-curve/` folder into the skills directory your Claude client reads from. (This path differs by product — Claude Code, Claude Desktop/Cowork, and claude.ai each have their own convention; check your client's docs for where custom skills live.)
3. That's it — no build step, no dependencies beyond Python 3. Claude will pick up `SKILL.md` and know when to use it.

You can also run the tool directly from the command line without Claude at all:

```bash
python scripts/eiopa_curves.py spot --country Germany --term 10 --date 2024-12-31
```

## Example commands

| Question | Command |
|---|---|
| One rate | `spot --country Germany --term 10 --date 2024-12-31` |
| Several tenors at once | `spot --country DE --term 1,5,10,30 --date 2024-12-31` |
| The whole curve | `curve --country DE --date 2024-12` |
| One tenor over time | `series --country IT --term 10 --from 2020 --freq year` |
| Countries side by side | `compare --countries DE,UK,US --term 10 --date latest --vs DE` |
| Implied forwards | `forward --country DE --start 5 --tenor 10 --date 2024-12-31` |
| SCR stressed curves | `shock --country DE --date 2024-12-31 --va --full` |
| UFR, LLP, alpha, CRA, VA | `params --country DE --date 2024-12-31` |
| What exists | `coverage`, `coverage --country BR` |
| Structural facts | `facts` |

Add `--va` for the `with_VA` curve (default is `no_VA`), `--df` for discount factors, and `--json` for machine-readable decimal output. Full details, caveats, and known traps (one curve per currency, not per sovereign; shifting country/currency labels over time; non-constant coverage) are documented in `SKILL.md`.

## Keeping the data current

EIOPA publishes a new release monthly. To refresh the bundled pack against a fresh checkout of EIOPA's published data:

```bash
python scripts/eiopa_curves.py pack --data-dir <path-to-EIOPA_all_curves-checkout>/data
```

Run the test suite afterward — `python scripts/tests/test_eiopa_curves.py` — which includes a snapshot-freshness check.

## Running tests

```bash
python scripts/tests/test_eiopa_curves.py
```

Pure stdlib `unittest`, no `pytest`, no network. Pins known rates and verifies every structural claim in `SKILL.md` against the bundled data.

## License / data source

Underlying rates are published by [EIOPA](https://www.eiopa.europa.eu/) (European Insurance and Occupational Pensions Authority) under its Solvency II risk-free rate methodology. Check EIOPA's terms for redistribution of the underlying data before republishing this repo publicly. [Add your own license here for the code/skill wrapper itself.]
