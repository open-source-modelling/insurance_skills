---
name: historic-eiopa-yield-curve
description: Answer any question about historic EIOPA risk-free rate (RFR) yield curves - a spot rate for a country and tenor at a reference date, a whole term structure, a tenor through time, countries side by side, implied forwards, discount factors, the Solvency II stressed curves, or curve parameters (UFR, LLP, alpha, CRA, VA). It carries the full published history with it - every monthly release since 2014-12-31, all curves, terms 1-150 years, no_VA and with_VA - so it needs no dataset, database, network or extra package. Use it whenever someone asks about a yield, spot rate, discount rate, risk-free rate, term structure, forward rate, EIOPA curve, RFR, Solvency II discount curve, UFR, last liquid point, volatility adjustment, or "what was the 10-year rate for Italy in June 2020" - even when they do not name EIOPA, and even when it sounds like a one-line lookup. It carries the country-label, coverage and one-curve-per-currency traps that make hand-written queries quietly return wrong or empty answers.
---

# EIOPA yield curve lookups

This skill is self-contained. `scripts/data/curves.pack` holds every rate EIOPA
has published — every monthly release, all curves, terms 1–150 years, both
`no_VA` and `with_VA` — in about 370 KB, and `scripts/eiopa_curves.py` reads it
and nothing else.

```bash
python <skill>/scripts/eiopa_curves.py <command> [options]
```

**No repo, no database, no network, no third-party package.** Copy those two
files anywhere Python 3 runs and every command works. Do not go looking for a
`yield_curves.csv` or an `EIOPA_all_curves` checkout — the skill ignores them
deliberately. A query costs about 150 ms and writes nothing.

**The pack is a snapshot, and that is the one way this skill can be confidently
wrong.** It cannot know about releases published after it was built. Run
`coverage` or `facts` for the live figures — both print the snapshot date and the
last release held, and `--json` carries `snapshot_generated` and
`latest_release_in_snapshot`. Say which release the data stops at whenever
recency could matter: `latest` means the newest release *in this snapshot*, not
the newest EIOPA has issued.

`facts` prints the structural claims — which currencies share a curve, which
months carry a country-specific volatility adjustment, when coverage changed,
where the currency-label era ends. Prefer it over any figure written down here.

## Commands

| Question | Command |
|---|---|
| One rate | `spot --country Germany --term 10 --date 2024-12-31` |
| Several tenors at once | `spot --country DE --term 1,5,10,30 --date 2024-12-31` |
| The whole curve | `curve --country DE --date 2024-12` (add `--full` for all 150 terms) |
| One tenor over time | `series --country IT --term 10 --from 2020 --freq year` |
| Countries side by side | `compare --countries DE,UK,US --term 10 --date latest --vs DE` |
| Implied forwards | `forward --country DE --start 5 --tenor 10 --date 2024-12-31` |
| SCR stressed curves | `shock --country DE --date 2024-12-31 --va --full` |
| UFR, LLP, alpha, CRA, VA | `params --country DE --date 2024-12-31` |
| What exists | `coverage`, `coverage --date 2014-12-31`, `coverage --country BR` |
| Structural facts | `facts` — shared curves, VA exceptions, coverage changes |

Shared flags, usable before or after the subcommand:

- `--va` — the `with_VA` curve. Default is `no_VA`.
- `--df` — add discount factors, `(1+r)^-t`. Reach for this whenever the question
  is really about valuing cashflows; computing them by hand is error-prone and
  the tool already has the compounding convention right.
- `--json` — machine-readable, rates as raw decimals. Use this when you need to
  compute with the numbers; the default text output is for reading.

**Fractional tenors work.** `--term 7.5` interpolates log-linearly on discount
factors — a constant forward between the published whole-year nodes — and marks
the row `†` with a note that it is derived rather than published. Keep that
distinction in your answer. Anything under a year comes back at the one-year
rate, which is what a constant forward over the first year implies. `shock`
refuses fractional terms outright, because the regulatory factors are defined
per whole year and rounding 7.5 to 7 would stress a different tenor than the one
asked about.

Dates are forgiving: `2024-12-31`, `2024-12`, `Dec 2024`, `2024Q4`, `2024`
(year-end), `latest`. Anything that is not a published month-end snaps to the
nearest release and the output says so — repeat that in your answer rather than
silently reporting a different month than the one asked about.

Countries are forgiving too: country code, currency code, or full name
(`Germany`, `DE`, `Deutschland`; `UK`, `GB`, `Britain`; `EU`, `eurozone`).

## What will bite you

**One curve per currency, not per sovereign.** This is the big one. EIOPA
publishes a risk-free curve per *currency*, so countries sharing a currency have
byte-identical curves and a spread between two of them is always exactly zero.
These are swap-derived rates with no credit component, so no BTP–Bund style
spread exists here at all — say that plainly rather than reporting zeros. The
`with_VA` curve can differ, but only where EIOPA set a country-specific
volatility adjustment. `compare` detects identical results and explains them;
leave that note in.

**Country labels change partway through.** Early releases label curves by
*currency* (`AUD`, `GBP`, `CHF`), later ones by *country* (`AU`, `UK`, `CH`), so
a naive filter on `AU` silently returns nothing for the early years. The tool
normalises both directions and its provenance line reports the raw label.

**Coverage is not constant.** Curves get dropped; a question about a country in
a later year may have no answer, and that is a fact about EIOPA's publication
rather than a gap. Run `coverage --country XX` before asserting a trend "ended".

**Which currencies share a curve, which months carry a country-specific VA, and
exactly when coverage changed are all derived from the data — run `facts`.**
They are deliberately not written out here, because that is how they went stale
once already. If any documentation disagrees with `facts`, `facts` is right.

**Rates are decimals in the data, percent in the output.** `0.02267` is 2.267%.
The text output renders percent; `--json` gives decimals. Do not multiply twice.
UFR is in percent, CRA and VA in basis points.

**Forwards are derived, not published.** EIOPA publishes spot curves only. The
tool derives forwards assuming annual compounding:
`f = ((1+s2)^t2 / (1+s1)^t1)^(1/(t2-t1)) - 1`. Keep the compounding note in your
answer — a different convention is the usual reason two people get different
forwards from the same curve.

**Stressed curves are reconstructed, and say so.** `shock` produces EIOPA's
Solvency II interest-rate stresses, which appear in no published dataset — the
workbook sheets hold uncalculated formulas that read back as zeros. The tool
computes them from the regulatory factors, and its output already carries the
three things that make a hand-derived stress wrong: every variant is built from
the **`no_VA`** curve with the VA added back afterwards (shocking the `with_VA`
curve understates the down stress by roughly factor × VA, about 7 bp at a 23 bp
VA); the down shock leaves negative rates untouched; and the up shock has a
one-percentage-point floor that governs any rate below about 2.4%. Keep the
"reconstructed, not published" framing, and remember an SCR needs the whole
stressed structure — `--full` — not one node.

**Two things remain outside this skill.** Say so plainly rather than improvising.

- *Exact Smith-Wilson reconstruction* — evaluating EIOPA's own curve between
  nodes, or extrapolating past the last liquid point — is a separate skill. The
  log-linear interpolation here is a good approximation and honestly labelled,
  but it is not EIOPA's construction.
- *Sovereign spreads* are not merely absent but impossible: these curves carry no
  credit component at all.

## Answering well

Give the number, then the two facts that make it checkable: which reference date
it came from and which curve type. The tool prints a `Source:` line for exactly
this — carry it through.

Default to `no_VA` unless the person's context is clearly a Solvency II
technical-provisions calculation, where `with_VA` is often what they mean. If it
matters and you cannot tell, give the `no_VA` figure and note what the `with_VA`
one is; that is faster than asking.

Prefer one call with a list (`--term 1,5,10,30`, `--countries DE,FR,IT`) over
several calls. For a span longer than about three years use
`--freq year` or `--freq quarter` — a decade of monthly rows buries the
point rather than making it.

When a question is really about the shape of a curve or a long history, put the
figures in a small table and say what they show. A yield curve inverting, or a
tenor moving 300 bp over three years, deserves a sentence of interpretation
alongside the numbers.

## Further reference

`references/dataset.md` covers the currency/country label table, the pack format,
the shock formulas and their traps, and what this snapshot deliberately does not
contain. Read it when a question turns on the data's structure rather than a
number.

`python3 scripts/tests/test_eiopa_curves.py` runs the suite in a couple of
seconds — stdlib unittest, no pytest, no network. It pins known rates, proves the
skill answers from an isolated copy while ignoring a planted local CSV, and
checks every structural claim above against the data. Tests needing an
`EIOPA_all_curves` checkout (pack fidelity, the Excel shock formulas) skip
themselves unless `EIOPA_REPO` points at one. A `TestSnapshotFreshness` failure
is not a bug — it means the pack is behind and needs rebuilding with
`pack --data-dir <checkout>/data`, then `--regenerate-golden`.
