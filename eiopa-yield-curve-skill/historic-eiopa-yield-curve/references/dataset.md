# Historic EIOPA yield curves — data reference

Everything described here is inside the skill. There is no dataset to locate, no
repository to check out and no file to read alongside these notes; the numbers
live in `scripts/data/curves.pack` and the tool reads only that.

Read this file when a question turns on exactly which curves existed when, when
you need the pack's internals, or when someone asks for something this snapshot
deliberately does not contain.

## Contents

1. [What the snapshot contains](#what-the-snapshot-contains)
2. [Country codes and the label change](#country-codes-and-the-label-change)
3. [Coverage over time](#coverage-over-time)
4. [Curve types and the volatility adjustment](#curve-types-and-the-volatility-adjustment)
5. [The pack format](#the-pack-format)
6. [Derived, not published](#derived-not-published)
7. [What is not in here](#what-is-not-in-here)
8. [Refreshing the snapshot](#refreshing-the-snapshot)

## What the snapshot contains

Monthly releases from 2014-12-31 onward; every curve EIOPA publishes; whole-year
terms 1 to 150; both `no_VA` and `with_VA` in every release; plus the parameter
table (instrument, coupon frequency, LLP, convergence, UFR, alpha, CRA, VA).

**Run `facts` for the counts and dates.** They are not written here on purpose:
release counts, curve counts and the date range change every time the snapshot is
refreshed, and a number in prose is a number that goes stale. `facts` derives
them from the pack on every run, along with the structural claims below.

Conventions worth stating in any answer that quotes a number:

- **Spot rates are annually compounded decimals.** `0.02267` is 2.267%. The text
  output renders percent; `--json` returns decimals.
- **UFR is in percent** (`3.3` means 3.3%). **CRA and VA are in basis points**
  (`23.0` means 23 bp). Mixing those units is an easy and invisible mistake.
- Reference dates are month-ends. Nothing falls between them.

`coverage` prints the live figures from the pack itself, so prefer running it
over trusting the table above, which is accurate as of the snapshot date.

## Country codes and the label change

EIOPA switched labelling conventions after the 2015-10-31 release. The pack
stores the modern country code and keeps the original label alongside, so
lookups work in both eras and the provenance line can report what the source
actually said.

| Currency label (2014-12-31 … 2015-10-31) | Country code (2015-11-30 onward) | Name |
|---|---|---|
| AUD | AU | Australia |
| BGN | BG | Bulgaria |
| BRL | BR | Brazil |
| CAD | CA | Canada |
| CHF | CH | Switzerland |
| CLP | CL | Chile |
| CNY | CN | China |
| COP | CO | Colombia |
| CZK | CZ | Czechia |
| DKK | DK | Denmark |
| GBP | UK | United Kingdom |
| HKD | HK | Hong Kong |
| HRK | HR | Croatia |
| HUF | HU | Hungary |
| INR | IN | India |
| ISK | IS | Iceland |
| JPY | JP | Japan |
| KRW | KR | South Korea |
| LIC | LI | Liechtenstein |
| LTL | LT | Lithuania |
| LVL | LV | Latvia |
| MXN | MX | Mexico |
| MYR | MY | Malaysia |
| NOK | NO | Norway |
| NZD | NZ | New Zealand |
| PLN | PL | Poland |
| RON | RO | Romania |
| RUB | RU | Russia |
| SEK | SE | Sweden |
| SGD | SG | Singapore |
| THB | TH | Thailand |
| TRY | TR | Turkey |
| TWD | TW | Taiwan |
| USD | US | United States |
| ZAR | ZA | South Africa |

Euro-area members kept their country codes throughout: AT, BE, CY, DE, EE, ES,
FI, FR, GR, IE, IT, LU, MT, NL, PT, SI, SK, plus `EU` for the euro curve itself.
Note `UK`, not `GB`.

## Coverage over time

The published set is not constant: EIOPA drops curves, and countries move onto a
different currency's curve when they adopt the euro. Both show up in the data,
and both are derived rather than listed here — `facts` prints every release where
the set moved and exactly what was added or dropped, and `coverage --country XX`
gives one country's span and its missing months.

Two consequences worth carrying into answers. A country's history can simply stop
partway through, which is a fact about EIOPA's publication rather than a gap in
this snapshot — check before describing a trend as having "ended". And a country
that adopts the euro becomes identical to the euro curve from that point,
differing from it before.

## Curve types and the volatility adjustment

`no_VA` is the base risk-free curve. `with_VA` adds the volatility adjustment.

Because EIOPA builds curves per currency, countries sharing a currency share a
curve exactly — on `no_VA`, at every tenor, in every release. The `with_VA` curve
is the only place they can diverge, and only where EIOPA set a country-specific
volatility adjustment, which is rare.

Which countries share a curve, and which months carry a country-specific VA, are
derived by `facts` rather than listed here. The tool detects the grouping on a
few probe tenors and the test suite re-verifies each reported group over its full
150-term structure, so the claim is checked rather than asserted.

The consequence: a question phrased as a spread between two sovereigns sharing a
currency has no answer in this data, and a table of identical numbers is correct
output rather than a bug.

## The pack format

`scripts/data/curves.pack` is roughly 370 KB and holds every rate above.

Layout: `magic | uint32 header length | UTF-8 JSON header | int32 body`, the
whole thing LZMA-compressed. The header carries the release list, the country
vocabulary, the original source labels and the parameter table. The body holds
one run per (date, curve type, country) block, in header order.

Two properties make it small. EIOPA publishes to five decimal places, so rates
are exactly representable as integers at a scale of 100,000. And yield curves are
smooth, so delta-encoding along the term axis leaves mostly small numbers for
LZMA to squeeze. It is 200× smaller than the equivalent CSV and lossless.

There is no database and no cache. Decompressing the whole history and building
the block lookup takes about 90 ms — faster than opening a prebuilt index would
be — so the pack is read fresh on every run and nothing is written to disk.

## Derived, not published

Two things the tool computes rather than looks up. Both are honest arithmetic on
published inputs, and both are labelled as derived in the output — say so when
quoting them.

**Fractional tenors.** EIOPA publishes whole years only, so `--term 7.5`
interpolates log-linearly on discount factors: `ln DF` is linear between the two
bracketing nodes, which is the same as assuming a constant forward rate across
the gap and keeps the result arbitrage-free. Rows are marked `†`. Below one year
the bracket is `DF(0) = 1`, so any term under a year returns the one-year rate.
Terms beyond the end of the curve are refused rather than extrapolated.

**Solvency II stressed curves.** The `shock` command
produces EIOPA's interest-rate stresses. They are not published data: the
workbook sheets hold uncalculated formulas, so `openpyxl(data_only=True)` reads
every cell as `0` and the obvious approach yields a confident zero. The tool
computes them instead, from formulas and factors that are both regulatory
constants:

```
no_VA   down:  s < 0 ? s : s - f_down * |s|
with_VA down:  (s < 0 ? s : s - f_down * |s|) + va
no_VA   up:    s + MAX(0.01, f_up * |s|)
with_VA up:    s + MAX(0.01, f_up * |s|) + va
```

`s` is the `no_VA` spot, `va` the volatility adjustment as a decimal, and the
result is rounded to five decimals — EIOPA's rounding, not cosmetic. Three
details are each a way to be plausibly wrong:

- Every variant starts from the **no_VA** curve. The `with_VA` stress adds the VA
  back *after* shocking; deriving it from the `with_VA` curve understates the
  down stress by `f_down × va` — about 7 bp at a 23 bp VA.
- The down shock leaves negative rates untouched; only the VA is added.
- The up shock has a one-percentage-point floor that binds whenever
  `f_up × |s| < 0.01`, which at a 0.42 factor means any rate below about 2.4%.

The maturity factors are embedded in the tool, so this works with no workbook
present. They are tabulated for maturities 1–20 and run linearly to 0.20 at 90
years, staying there — the workbook's own construction, so the derived values
match rather than approximate. At maturity 10 they are 0.31 down and 0.42 up.

Both the factors and the formulas were checked against the 2015-03, 2018-11,
2022-06, 2024-12 and 2026-07 workbooks: one table and one formula across the
whole span. The test suite re-checks all 150 factors and reconstructs the full
term structure in both directions and both curve types whenever a workbook is
reachable, so a regulatory change fails loudly rather than producing stale
stresses quietly.

Shocks are defined per whole year, so `shock` refuses fractional terms instead of
rounding them.

## What is not in here

Being explicit about the boundaries, because the failure mode is improvising
something plausible rather than saying it is unavailable.

**Anything published after the snapshot date.** The pack cannot know about later
releases. `coverage` prints the snapshot date and the last release it holds, and
`--json` carries `snapshot_generated` and `latest_release_in_snapshot`. When
someone asks for "the latest" or "now", say which release the data actually
stops at.

**Exact Smith-Wilson reconstruction** — evaluating EIOPA's own curve between the
published nodes, or extrapolating past the last liquid point — is **a separate
skill**, not this one. The log-linear interpolation described below is a sound
approximation and is labelled as derived, but it is not EIOPA's construction.
Do not improvise that here.

**Sovereign credit spreads.** These are swap-derived risk-free curves. They carry
no sovereign credit component, so no BTP–Bund style spread can be computed from
them at all — see the curve-types section above.

## Refreshing the snapshot

```bash
python scripts/eiopa_curves.py pack --data-dir <EIOPA_all_curves>/data
```

This is the one command that reads anything outside the skill, and it is a
maintenance step — never part of answering a question. It rebuilds the pack from
an `EIOPA_all_curves` checkout whose pipeline has loaded newer months, after
which the skill is repackaged.

The rebuild is lossless: the test suite regenerates a pack from the source CSVs
and compares it to the bundled one rate for rate, and separately samples
individual rates straight from the CSV. Those tests skip themselves when no
checkout is reachable, so they run for whoever maintains the data and stay quiet
for everyone else.

After refreshing, update the release count and date range quoted in `SKILL.md`
and in [What the snapshot contains](#what-the-snapshot-contains), then re-pin the
test baseline with `--regenerate-golden`. `TestSnapshotFreshness` fails until
that is done, which is the reminder.
