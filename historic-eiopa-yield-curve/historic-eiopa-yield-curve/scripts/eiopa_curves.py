#!/usr/bin/env python3
"""
eiopa_curves.py - query the full EIOPA risk-free rate history.

This tool is self-contained. Every published rate lives in `data/curves.pack`
beside this script: 140 monthly releases, 53 curves, terms 1-150 years, both the
no_VA and with_VA variants - 2.16 million observations in about 370 KB. It reads
nothing else, writes nothing, and needs no network, no database and no
third-party package. Copy these two files anywhere and it works.

Why there is no index
---------------------
An earlier version mirrored the data into SQLite. That made sense against a
76 MB CSV, but against the pack it is pure overhead: decompressing the whole
history and building the lookup takes about 90 ms, which is faster than opening
a prebuilt index, and it avoids writing an 89 MB cache file onto a machine that
may not want one or may not let us. So the pack is read straight into memory on
every run.

The pack's shape is what makes that cheap. EIOPA publishes to five decimal
places and yield curves are smooth, so rates are stored as integers and
delta-encoded along the term axis, leaving mostly small numbers for LZMA to
squeeze. The result is lossless: rebuilding from the pack reproduces the source
CSVs rate for rate.

Freshness
---------
The pack is a snapshot taken when the skill was built, so it cannot know about
releases published since. Every command can report the snapshot date, `coverage`
always does, and answers about recent months should say which release the data
stops at. To refresh it, run `pack` against an updated EIOPA_all_curves
checkout and rebuild the skill.

Usage examples:
    python eiopa_curves.py spot     --country Germany --term 10 --date 2024-12-31
    python eiopa_curves.py curve    --country DE --date 2024-12 --terms 1,5,10,20,30
    python eiopa_curves.py series   --country IT --term 10 --from 2020 --freq year
    python eiopa_curves.py compare  --countries DE,UK,US --term 10 --date latest --vs DE
    python eiopa_curves.py forward  --country DE --start 5 --tenor 10 --date 2024-12-31
    python eiopa_curves.py shock    --country DE --date 2024-12-31 --va --full
    python eiopa_curves.py params   --country DE --date 2024-12-31
    python eiopa_curves.py coverage --date 2014-12-31
    python eiopa_curves.py pack --data-dir /path/to/EIOPA_all_curves/data
"""
from __future__ import annotations

import argparse
import array
import csv
import json
import lzma
import math
import os
import re
import struct
import sys
from pathlib import Path

PACK_MAGIC = b"EIOPACRV"
PACK_VERSION = 1
PACK_SCALE = 100000  # EIOPA publishes spot rates to five decimal places
CURVE_TYPES = ["no_VA", "with_VA"]

# --------------------------------------------------------------------------
# Solvency II interest-rate shock factors
# --------------------------------------------------------------------------
#
# The stressed term structures used for the SCR interest-rate sub-module are not
# part of the published spot data, but they are fully reconstructible: the
# factors are a regulatory constant and the formulas are arithmetic.
#
# Both were checked against the Term_Structures workbooks for 2015-03, 2018-11,
# 2022-06, 2024-12 and 2026-07: one identical factor table and one identical
# formula across the whole span. A test re-checks this whenever a workbook is
# reachable, so a future regulatory change fails loudly instead of silently
# producing stale stresses.
#
# Only maturities 1-20 are tabulated. From 20 to 90 the factors run linearly to
# 0.20 and stay there - that is exactly how the workbook computes them, so
# deriving them here reproduces its values rather than approximating them.
_SHOCK_DOWN_20 = (0.75, 0.65, 0.56, 0.50, 0.46, 0.42, 0.39, 0.36, 0.33, 0.31,
                  0.30, 0.29, 0.28, 0.28, 0.27, 0.28, 0.28, 0.28, 0.29, 0.29)
_SHOCK_UP_20 = (0.70, 0.70, 0.64, 0.59, 0.55, 0.52, 0.49, 0.47, 0.44, 0.42,
                0.39, 0.37, 0.35, 0.34, 0.33, 0.31, 0.30, 0.29, 0.27, 0.26)
SHOCK_TAIL_TERM = 90       # factors reach the floor here
SHOCK_TAIL_VALUE = 0.20
SHOCK_UP_FLOOR = 0.01      # absolute minimum upward shock, in decimal


def _shock_table(head: tuple) -> tuple:
    out = list(head)
    anchor = head[-1]
    span = SHOCK_TAIL_TERM - len(head)
    for t in range(len(head) + 1, SHOCK_TAIL_TERM + 1):
        out.append(anchor + (SHOCK_TAIL_VALUE - anchor) * (t - len(head)) / span)
    out += [SHOCK_TAIL_VALUE] * (150 - SHOCK_TAIL_TERM)
    return tuple(out)


SHOCK_DOWN = _shock_table(_SHOCK_DOWN_20)
SHOCK_UP = _shock_table(_SHOCK_UP_20)


def shocked_rate(s: float, term: int, direction: str, va: float = 0.0) -> float:
    """EIOPA's stressed spot rate, reproducing the workbook formulas exactly.

    Three details matter, and each is a way to be plausibly wrong:

    - Every variant starts from the **no_VA** curve. The with_VA stress adds the
      volatility adjustment back after shocking; shocking the with_VA curve
      instead understates the down stress by about `factor x va`.
    - The down shock leaves negative rates untouched - only the VA is added.
    - The up shock carries an absolute one-percentage-point floor, which binds
      whenever the proportional shock is smaller than that. At a 0.42 factor
      the floor governs any rate below roughly 2.4%.

    The final rounding to five decimals is EIOPA's, not cosmetic: it is what the
    published sheets contain.
    """
    f = (SHOCK_DOWN if direction == "down" else SHOCK_UP)[term - 1]
    if direction == "down":
        stressed = s if s < 0 else s - f * abs(s)
    else:
        stressed = s + max(SHOCK_UP_FLOOR, f * abs(s))
    return round(stressed + va, 5)


def up_shock_floor_binds(s: float, term: int) -> bool:
    return SHOCK_UP[term - 1] * abs(s) < SHOCK_UP_FLOOR


def discount_factor(rate: float, term: float) -> float:
    """EIOPA spot rates are annually compounded, so DF = (1+r)^-t."""
    return (1.0 + rate) ** (-term)

# --------------------------------------------------------------------------
# Country vocabulary
# --------------------------------------------------------------------------

# Currency-era label -> modern EIOPA country code. Read off the data, not
# guessed: these are exactly the labels that appear on or before 2015-10-31 and
# never afterwards.
CURRENCY_TO_COUNTRY = {
    "AUD": "AU", "BGN": "BG", "BRL": "BR", "CAD": "CA", "CHF": "CH",
    "CLP": "CL", "CNY": "CN", "COP": "CO", "CZK": "CZ", "DKK": "DK",
    "EUR": "EU", "GBP": "UK", "HKD": "HK", "HRK": "HR", "HUF": "HU",
    "INR": "IN", "ISK": "IS", "JPY": "JP", "KRW": "KR", "LIC": "LI",
    "LTL": "LT", "LVL": "LV", "MXN": "MX", "MYR": "MY", "NOK": "NO",
    "NZD": "NZ", "PLN": "PL", "RON": "RO", "RUB": "RU", "SEK": "SE",
    "SGD": "SG", "THB": "TH", "TRY": "TR", "TWD": "TW", "USD": "US",
    "ZAR": "ZA",
}

COUNTRY_NAMES = {
    "AT": "Austria", "AU": "Australia", "BE": "Belgium", "BG": "Bulgaria",
    "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "CY": "Cyprus", "CZ": "Czechia",
    "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "ES": "Spain",
    "EU": "Euro area", "FI": "Finland", "FR": "France", "GR": "Greece",
    "HK": "Hong Kong", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland",
    "IN": "India", "IS": "Iceland", "IT": "Italy", "JP": "Japan",
    "KR": "South Korea", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta", "MX": "Mexico",
    "MY": "Malaysia", "NL": "Netherlands", "NO": "Norway",
    "NZ": "New Zealand", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "RU": "Russia", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan",
    "UK": "United Kingdom", "US": "United States", "ZA": "South Africa",
}

# Spellings people actually type. Keys are lowercased and stripped of
# non-alphanumerics before lookup, so "euro-area" and "Euro Area" both land here.
EXTRA_ALIASES = {
    "gb": "UK", "gbr": "UK", "britain": "UK", "greatbritain": "UK",
    "unitedkingdom": "UK", "england": "UK",
    "usa": "US", "unitedstates": "US", "unitedstatesofamerica": "US",
    "america": "US",
    "eurozone": "EU", "euroarea": "EU", "euro": "EU", "emu": "EU",
    "czechrepublic": "CZ", "southkorea": "KR", "korea": "KR",
    "republicofkorea": "KR", "holland": "NL", "thenetherlands": "NL",
    "russianfederation": "RU", "turkiye": "TR", "tuerkiye": "TR",
    "china": "CN", "prc": "CN", "hongkong": "HK", "swiss": "CH",
    "deutschland": "DE", "espana": "ES", "italia": "IT",
    "oesterreich": "AT", "osterreich": "AT", "nederland": "NL",
}


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _build_alias_map() -> dict:
    m = {}
    for code, name in COUNTRY_NAMES.items():
        m[_norm_key(code)] = code
        m[_norm_key(name)] = code
    for cur, code in CURRENCY_TO_COUNTRY.items():
        m.setdefault(_norm_key(cur), code)
    m.update({_norm_key(k): v for k, v in EXTRA_ALIASES.items()})
    return m


ALIASES = _build_alias_map()


def resolve_country(raw: str) -> str:
    """Map anything a person might type onto the canonical EIOPA country code.

    Failing loudly with near-misses listed beats returning an empty result set:
    an empty table reads as "no data for that month" rather than "you spelled it
    in a way I did not recognise", and that mistake propagates silently.
    """
    key = _norm_key(raw)
    if ALIASES.get(key):
        return ALIASES[key]
    stem = key[:3]
    near = sorted({c for k, c in ALIASES.items()
                   if c and stem and (k.startswith(stem) or stem in k)})[:8]
    hint = f" Closest matches: {', '.join(near)}." if near else ""
    raise SystemExit(
        f"Unknown country '{raw}'. This dataset covers {len(COUNTRY_NAMES)} EIOPA "
        f"curves; give a country code, a currency code, or a full name."
        + hint + " Run `coverage` to list them all.")


def canonical_country(raw_label: str) -> str:
    """Normalise a label as stored in the source CSV to the modern country code."""
    return CURRENCY_TO_COUNTRY.get(raw_label, raw_label)


def to_dt(s: str) -> int:
    return int(s.replace("-", ""))


def from_dt(i: int) -> str:
    return f"{i // 10000:04d}-{(i // 100) % 100:02d}-{i % 100:02d}"


# --------------------------------------------------------------------------
# The pack
# --------------------------------------------------------------------------
#
# Layout: magic | uint32 header length | UTF-8 JSON header | int32 delta body,
# LZMA-compressed as a whole. The header carries the release list, the country
# vocabulary, the raw source labels and the parameter table. The body holds one
# delta run per (date, curve type, country) block, in header order.

def bundled_pack() -> Path:
    return Path(__file__).resolve().parent / "data" / "curves.pack"


def read_pack(path: Path):
    if not path.exists():
        raise SystemExit(
            f"Curve data missing: expected the packed history at {path}. "
            f"This file ships with the skill; if it has been removed, restore it "
            f"or regenerate it with `pack --data-dir <EIOPA_all_curves>/data`.")
    blob = lzma.decompress(path.read_bytes())
    if blob[:len(PACK_MAGIC)] != PACK_MAGIC:
        raise SystemExit(f"{path} is not an EIOPA curve pack.")
    off = len(PACK_MAGIC)
    (hlen,) = struct.unpack("<I", blob[off:off + 4])
    off += 4
    header = json.loads(blob[off:off + hlen].decode())
    if header.get("version") != PACK_VERSION:
        raise SystemExit(f"{path} has pack version {header.get('version')}; this "
                         f"tool expects {PACK_VERSION}.")
    body = array.array("i")
    body.frombytes(blob[off + hlen:])
    return header, body


def write_pack(data_dir: Path, out_path: Path, quiet: bool = False) -> Path:
    """Build a pack from an EIOPA_all_curves `data/` folder.

    This is the one command that reads the extraction repo. It is a maintenance
    step run when refreshing the skill, never part of answering a question.
    """
    import datetime

    yc = data_dir / "yield_curves.csv"
    cp = data_dir / "curve_parameters.csv"
    if not yc.exists():
        raise SystemExit(f"Cannot find {yc}; nothing to pack.")

    blocks, labels = {}, {}
    with open(yc, newline="") as fh:
        for r in csv.DictReader(fh):
            raw = r["country"]
            code = canonical_country(raw)
            date = r["reference_date"]
            span = labels.setdefault((code, raw), [date, date])
            span[0], span[1] = min(span[0], date), max(span[1], date)
            blocks.setdefault((date, r["curve_type"], code), {})[
                int(float(r["term_index"]))] = int(
                    round(float(r["spot_rate"]) * PACK_SCALE))

    keys = sorted(blocks)
    releases = sorted({k[0] for k in keys})
    countries = sorted({k[2] for k in keys})
    body, index = array.array("i"), []
    for date, ctype, code in keys:
        b = blocks[(date, ctype, code)]
        terms = sorted(b)
        prev = 0
        for t in terms:
            body.append(b[t] - prev)
            prev = b[t]
        index.append([releases.index(date), CURVE_TYPES.index(ctype),
                      countries.index(code), len(terms), terms[0]])

    params = {"columns": [], "rows": []}
    if cp.exists():
        with open(cp, newline="") as fh:
            rd = csv.DictReader(fh)
            params["columns"] = rd.fieldnames or []
            for r in rd:
                r["country"] = canonical_country(r["country"])
                params["rows"].append([r[c] for c in params["columns"]])

    header = {
        "format": "eiopa-curves-pack",
        "version": PACK_VERSION,
        "generated": datetime.date.today().isoformat(),
        "scale": PACK_SCALE,
        "releases": releases,
        "countries": countries,
        "curve_types": CURVE_TYPES,
        "index": index,
        "labels": [[c, raw, lo, hi] for (c, raw), (lo, hi) in sorted(labels.items())],
        "params": params,
    }
    hb = json.dumps(header, separators=(",", ":")).encode()
    blob = PACK_MAGIC + struct.pack("<I", len(hb)) + hb + body.tobytes()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(lzma.compress(blob, preset=9 | lzma.PRESET_EXTREME))
    if not quiet:
        print(f"# packed {len(keys):,} curves / {len(body):,} rates from "
              f"{len(releases)} releases ({releases[0]} to {releases[-1]}) -> "
              f"{out_path} ({out_path.stat().st_size / 1e3:.0f} KB)", file=sys.stderr)
    return out_path


class Curves:
    """The whole published history, held in memory.

    Rates stay as packed integers until a caller asks for one, so the resident
    cost is the 8.6 MB int array rather than two million Python floats.
    """

    def __init__(self, header, body):
        self.generated = header.get("generated", "unknown")
        self.scale = header["scale"]
        self.releases = header["releases"]
        self.countries = header["countries"]
        self._body = body

        self._blocks = {}          # (code, k, date) -> (offset, n, first_term)
        self._at_date = {}         # date -> [codes]
        pos = 0
        for date_i, k, country_i, n, first_term in header["index"]:
            code, date = self.countries[country_i], self.releases[date_i]
            self._blocks[(code, k, date)] = (pos, n, first_term)
            if k == 0:
                self._at_date.setdefault(date, []).append(code)
            pos += n

        self._labels = {}
        for code, raw, lo, hi in header["labels"]:
            self._labels.setdefault(code, []).append((lo, hi, raw))

        self._va_exc = None
        pcols = header["params"]["columns"]
        self._params = {}
        if pcols:
            ci, di, ki = (pcols.index("country"), pcols.index("reference_date"),
                          pcols.index("curve_type"))
            for row in header["params"]["rows"]:
                self._params[(row[ci], row[di], row[ki])] = dict(zip(pcols, row))

    # -- lookups ----------------------------------------------------------

    def curve(self, code: str, date: str, k: int) -> dict:
        """Every published term for one curve, as {term: rate}."""
        blk = self._blocks.get((code, k, date))
        if blk is None:
            return {}
        pos, n, first = blk
        out, acc = {}, 0
        for j in range(n):
            acc += self._body[pos + j]
            out[first + j] = acc / self.scale
        return out

    def rate(self, code: str, date: str, k: int, term: int):
        blk = self._blocks.get((code, k, date))
        if blk is None:
            return None
        pos, n, first = blk
        j = term - first
        if j < 0 or j >= n:
            return None
        return sum(self._body[pos:pos + j + 1]) / self.scale

    def rate_at(self, code: str, date: str, k: int, term):
        """Rate at any term, returning (rate, interpolated).

        EIOPA publishes whole years only. A fractional term is interpolated
        log-linearly on discount factors, which is equivalent to assuming a
        constant forward rate between the two published nodes - the standard
        choice, and the one that stays arbitrage-free. The caller gets a flag
        rather than a silently-derived number, because presenting an
        interpolated rate as published is the mistake worth preventing.

        Below the one-year node the bracket is DF(0) = 1, which makes any term
        under a year come back at the one-year rate.
        """
        if float(term).is_integer():
            r = self.rate(code, date, k, int(term))
            if r is not None:
                return r, False
        curve = self.curve(code, date, k)
        if not curve or term <= 0 or term > max(curve):
            return None, False
        lo = int(term)
        hi = lo + 1
        ln_lo = 0.0 if lo == 0 else -lo * math.log(1.0 + curve[lo])
        ln_hi = -hi * math.log(1.0 + curve[hi])
        ln_df = ln_lo + (ln_hi - ln_lo) * (term - lo) / (hi - lo)
        return math.exp(-ln_df / term) - 1.0, True

    def series(self, code: str, k: int, term, lo: str, hi: str):
        out = []
        for d in self.releases:
            if lo <= d <= hi:
                r, _ = self.rate_at(code, d, k, term)
                if r is not None:
                    out.append((d, r))
        return out

    def countries_at(self, date: str) -> list:
        return sorted(self._at_date.get(date, []))

    def curve_exists(self, code: str, k: int, date: str) -> bool:
        return (code, k, date) in self._blocks

    def has(self, code: str) -> bool:
        return any(c == code for c, _, _ in self._blocks)

    def raw_label(self, code: str, date: str) -> str:
        for lo, hi, raw in self._labels.get(code, []):
            if lo <= date <= hi:
                return raw
        return code

    def labels(self, code: str) -> list:
        return sorted(self._labels.get(code, []))

    def params(self, code: str, date: str, ctype: str):
        return self._params.get((code, date, ctype))

    def dates_for(self, code: str) -> list:
        return [d for d in self.releases if (code, 0, d) in self._blocks]

    def max_term(self, code: str, k: int, date: str) -> int:
        blk = self._blocks.get((code, k, date))
        return 0 if blk is None else blk[2] + blk[1] - 1

    @property
    def n_observations(self) -> int:
        return len(self._body)

    # -- derived facts ----------------------------------------------------
    #
    # These used to be sentences in the documentation, which is how they went
    # stale the first time. Deriving them from the pack means a refreshed
    # snapshot updates them for free and no prose can drift out of step.

    def currency_groups(self, date: str, k: int = 0,
                        probe=(1, 10, 20)) -> list:
        """Countries whose curves coincide at this date.

        EIOPA builds one curve per currency, so these groups are the currency
        blocs as the data actually shows them - no hardcoded euro-area list to
        fall out of date when a country joins or leaves.

        A few probe tenors are enough to separate genuinely different curves;
        `verify_group` re-checks a group over its whole term structure when the
        answer needs to be certain rather than fast.
        """
        sig = {}
        for c in self.countries_at(date):
            key = tuple(self.rate(c, date, k, t) for t in probe)
            sig.setdefault(key, []).append(c)
        return sorted((sorted(v) for v in sig.values() if len(v) > 1),
                      key=len, reverse=True)

    def verify_group(self, codes: list, date: str, k: int = 0) -> bool:
        first = self.curve(codes[0], date, k)
        return all(self.curve(c, date, k) == first for c in codes[1:])

    def va_exceptions(self) -> dict:
        """{country: [dates]} where a country's with_VA curve departs from the
        peers it shares a no_VA curve with - i.e. a country-specific volatility
        adjustment. Detected on one tenor, then confirmed on the full curve."""
        if self._va_exc is None:
            found = {}
            for date in self.releases:
                for group in self.currency_groups(date, 0):
                    vals = {c: self.rate(c, date, 1, 10) for c in group}
                    counts = {}
                    for v in vals.values():
                        counts[v] = counts.get(v, 0) + 1
                    norm = max(counts, key=counts.get)
                    for c, v in vals.items():
                        if v is not None and norm is not None and abs(v - norm) > 1e-12:
                            found.setdefault(c, []).append(date)
            self._va_exc = {k: sorted(v) for k, v in sorted(found.items())}
        return self._va_exc

    def coverage_changes(self) -> list:
        """[(date, added, removed)] each time the published country set moves."""
        out, prev = [], None
        for d in self.releases:
            cur = set(self.countries_at(d))
            if prev is not None and cur != prev:
                out.append((d, sorted(cur - prev), sorted(prev - cur)))
            prev = cur
        return out

    def currency_era_end(self):
        """Last release that labelled curves by currency rather than country."""
        last = None
        for code, spans in self._labels.items():
            for lo, hi, raw in spans:
                if raw != code and (last is None or hi > last):
                    last = hi
        return last


def load(pack_path: Path | None = None) -> Curves:
    path = pack_path or bundled_pack()
    return Curves(*read_pack(path))


# --------------------------------------------------------------------------
# Date handling
# --------------------------------------------------------------------------

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def resolve_date(cv: Curves, raw: str):
    """Snap a loosely written date onto a published reference date.

    Returns (date, note). The note is set whenever snapping happened, so callers
    can tell the reader which month they are actually looking at. Quietly
    answering about a different month than the one asked for is the kind of
    error nobody catches downstream.
    """
    dates = cv.releases
    s = raw.strip().lower()
    if s in ("latest", "last", "newest", "current", "most recent"):
        return dates[-1], f"'{raw}' -> latest release in this snapshot {dates[-1]}"
    if s in ("earliest", "first", "oldest"):
        return dates[0], f"'{raw}' -> earliest release {dates[0]}"
    if s in dates:
        return s, None

    def hit(prefix, why):
        h = [d for d in dates if d.startswith(prefix)]
        if not h:
            return None
        return h[0], None if h[0] == s else f"{raw} -> {why + ' ' if why else ''}{h[0]}"

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return hit(f"{m.group(1)}-{m.group(2)}", "month-end") or _nearest(dates, s, raw)

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        p = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
        return hit(p, "") or _nearest(dates, p + "-28", raw)

    m = re.search(r"([a-z]{3,})\w*\s+(\d{4})|(\d{4})\s+([a-z]{3,})", s)
    if m:
        mon = (m.group(1) or m.group(4) or "")[:3]
        yr = m.group(2) or m.group(3)
        if mon in MONTHS and yr:
            r = hit(f"{yr}-{MONTHS[mon]:02d}", "")
            if r:
                return r

    m = re.search(r"(\d{4})\s*q([1-4])|q([1-4])\s*(\d{4})", s)
    if m:
        yr = m.group(1) or m.group(4)
        q = int(m.group(2) or m.group(3))
        r = hit(f"{yr}-{q * 3:02d}", "quarter-end")
        if r:
            return r

    m = re.fullmatch(r"(\d{4})", s)
    if m:
        h = [d for d in dates if d.startswith(m.group(1))]
        if h:
            return h[-1], f"{raw} -> year-end {h[-1]}"

    raise SystemExit(
        f"Could not read '{raw}' as a reference date. This snapshot runs "
        f"{dates[0]} to {dates[-1]}, monthly. Try 2024-12-31, 2024-12, "
        f"'Dec 2024', 2024Q4, 2024, or 'latest'.")


def _nearest(dates, target, raw):
    best = min(dates, key=lambda d: abs(_ord(d) - _ord(target)))
    return best, f"{raw} is not a published month-end; using nearest release {best}"


def _ord(d: str) -> int:
    y, m, dd = (int(x) for x in d.split("-"))
    return y * 372 + m * 31 + dd


def resolve_range(cv: Curves, frm, to):
    return (_edge(cv, frm, True) if frm else cv.releases[0],
            _edge(cv, to, False) if to else cv.releases[-1])


def _edge(cv: Curves, raw: str, lower: bool) -> str:
    s = raw.strip()
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-01-01" if lower else f"{s}-12-31"
    if re.fullmatch(r"\d{4}[-/]\d{1,2}", s):
        y, m = re.split(r"[-/]", s)
        return (f"{int(y):04d}-{int(m):02d}-01" if lower
                else f"{int(y):04d}-{int(m):02d}-31")
    return resolve_date(cv, s)[0]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def parse_terms(spec) -> list:
    """Accept `10`, `7.5`, `1,5,10.5`. Whole numbers stay ints so they print as
    `10y` rather than `10.0y`."""
    out = []
    for tok in str(spec).replace(" ", "").split(","):
        if not tok:
            continue
        try:
            v = float(tok)
        except ValueError:
            raise SystemExit(f"'{tok}' is not a term in years. Use e.g. 10, 7.5, "
                             f"or 1,5,10.")
        out.append(int(v) if v.is_integer() else v)
    if not out:
        raise SystemExit("No terms given.")
    return out


def fmt_term(t) -> str:
    return f"{t}y" if isinstance(t, int) else f"{t:g}y"


def pct(x, dp=3) -> str:
    return "n/a" if x is None else f"{x * 100:.{dp}f}%"


def bps(x, dp=1) -> str:
    return "n/a" if x is None else f"{x * 10000:+.{dp}f} bp"


def table(headers, rows) -> str:
    if not rows:
        return "_no rows_"
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    def line(cells):
        return "| " + " | ".join(str(c).ljust(x) for c, x in zip(cells, w)) + " |"
    return "\n".join([line(headers), "|" + "|".join("-" * (x + 2) for x in w) + "|"]
                     + [line(r) for r in rows])


def emit(args, payload, text):
    if getattr(args, "json", False):
        payload = dict(payload)
        payload.setdefault("snapshot_generated", args._cv.generated)
        payload.setdefault("latest_release_in_snapshot", args._cv.releases[-1])
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(text)


def label(code: str) -> str:
    return f"{COUNTRY_NAMES.get(code, code)} ({code})"


def provenance(cv: Curves, code: str, date: str, ctype: str) -> str:
    """Name the exact curve behind the numbers, including the raw label the
    source file used, so any figure can be traced back to its release."""
    raw = cv.raw_label(code, date)
    extra = f", labelled '{raw}' in that release" if raw != code else ""
    return f"Source: EIOPA RFR {date}, {ctype} curve{extra}."


def ctype_id(args) -> int:
    return 1 if getattr(args, "va", False) else 0


def ctype_name(args) -> str:
    return CURVE_TYPES[ctype_id(args)]


def no_data(cv: Curves, code: str, date: str, ctype: str) -> str:
    have = cv.countries_at(date)
    if code not in have:
        return (f"No curve for {label(code)} in the {date} release. That release "
                f"publishes {len(have)} curves: {', '.join(have)}.")
    return f"No {ctype} data for {label(code)} at {date}."


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def interpolation_note(any_interpolated: bool) -> str:
    if not any_interpolated:
        return ""
    return ("\n\n_Rows marked † are interpolated, not published: EIOPA publishes "
            "whole years only. Interpolation is log-linear on discount factors "
            "(constant forward between the neighbouring published nodes). Quote "
            "these as derived figures. Exact Smith-Wilson reconstruction of the "
            "curve between nodes is a separate skill._")


def cmd_spot(cv, args):
    code = resolve_country(args.country)
    date, note = resolve_date(cv, args.date)
    k, ctype = ctype_id(args), ctype_name(args)
    curve = cv.curve(code, date, k)
    if not curve:
        raise SystemExit(no_data(cv, code, date, ctype))

    want_df = getattr(args, "df", False)
    headers = ["Term", "Spot rate", "Decimal"] + (["Discount factor"] if want_df else [])
    rows, payload, interp = [], [], False
    for t in terms_or_die(args.term):
        v, is_interp = cv.rate_at(code, date, k, t)
        interp = interp or is_interp
        row = [fmt_term(t) + (" †" if is_interp else ""), pct(v),
               "n/a" if v is None else f"{v:.6f}"]
        if want_df:
            row.append("n/a" if v is None else f"{discount_factor(v, t):.6f}")
        rows.append(row)
        payload.append({"term": t, "spot_rate": v, "interpolated": is_interp,
                        "percent": None if v is None else v * 100,
                        "discount_factor": None if v is None
                        else discount_factor(v, t)})
    head = f"**{label(code)} - {ctype} spot rate at {date}**"
    if note:
        head += f"\n_{note}_"
    missing = [p["term"] for p in payload if p["spot_rate"] is None]
    foot = (f"\n\n_Outside the published range: "
            f"{', '.join(fmt_term(t) for t in missing)} (available 1-{max(curve)}y)._"
            if missing else "")
    emit(args, {"country": code, "reference_date": date, "curve_type": ctype,
                "rates": payload},
         head + "\n\n" + table(headers, rows) + foot + interpolation_note(interp)
         + "\n\n" + provenance(cv, code, date, ctype))


def terms_or_die(spec) -> list:
    return parse_terms(spec)


def cmd_curve(cv, args):
    code = resolve_country(args.country)
    date, note = resolve_date(cv, args.date)
    k, ctype = ctype_id(args), ctype_name(args)
    curve = cv.curve(code, date, k)
    if not curve:
        raise SystemExit(no_data(cv, code, date, ctype))

    if args.terms:
        terms = parse_terms(args.terms)
    elif args.full:
        terms = sorted(curve)
    else:
        terms = [t for t in (1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50) if t in curve]

    want_df = getattr(args, "df", False)
    headers = ["Term", "Spot rate"] + (["Discount factor"] if want_df else [])
    rows, out, interp = [], {}, False
    for t in terms:
        v, is_interp = cv.rate_at(code, date, k, t)
        interp = interp or is_interp
        row = [fmt_term(t) + (" †" if is_interp else ""), pct(v)]
        if want_df:
            row.append("n/a" if v is None else f"{discount_factor(v, t):.6f}")
        rows.append(row)
        out[t] = v
    head = f"**{label(code)} - {ctype} term structure at {date}**"
    if note:
        head += f"\n_{note}_"
    if not args.terms and not args.full:
        head += f"\n_key tenors shown; --full for all {len(curve)} terms_"
    emit(args, {"country": code, "reference_date": date, "curve_type": ctype,
                "curve": out},
         head + "\n\n" + table(headers, rows) + interpolation_note(interp)
         + "\n\n" + provenance(cv, code, date, ctype))


def cmd_series(cv, args):
    code = resolve_country(args.country)
    k, ctype = ctype_id(args), ctype_name(args)
    terms = parse_terms(args.term)
    if len(terms) > 1:
        raise SystemExit("series takes one term at a time; give a single --term.")
    term = terms[0]
    lo, hi = resolve_range(cv, args.frm, args.to)
    full = cv.series(code, k, term, lo, hi)
    if not full:
        raise SystemExit(
            f"No {ctype} {term}y history for {label(code)} between {lo} and {hi}. "
            f"Check what exists with: coverage --country {code}")

    # `full` is every published month in range; `pts` is what the chosen
    # frequency displays. Keeping them separate matters: the headline range, the
    # extremes and the start-to-end move are properties of the underlying
    # series, and computing them from the thinned sample quietly reports the low
    # of the Decembers as though it were the low of the series.
    grid = {"year": ("12",), "quarter": ("03", "06", "09", "12")}.get(args.freq)
    if grid:
        pts = [p for p in full if p[0][5:7] in grid]
        # Never drop the newest observation: a range ending mid-year has its most
        # recent point off the sampling grid, and silently losing it is exactly
        # the truncation this tool exists to prevent.
        if full[-1] not in pts:
            pts.append(full[-1])
        if not pts:
            pts = [full[0], full[-1]] if len(full) > 1 else list(full)
        pts.sort()
    else:
        pts = full
    off_grid = bool(grid) and pts[-1][0][5:7] not in grid

    out, prev = [], None
    for d, v in pts:
        marker = " *" if off_grid and d == pts[-1][0] else ""
        out.append([d + marker, pct(v), "" if prev is None else bps(v - prev)])
        prev = v

    lo_p, hi_p = min(full, key=lambda p: p[1]), max(full, key=lambda p: p[1])
    _, series_interp = cv.rate_at(code, full[-1][0], k, term)
    head = (f"**{label(code)} - {fmt_term(term)} {ctype} spot rate, "
            f"{full[0][0]} to {full[-1][0]}**")
    if series_interp:
        head += ("\n_interpolated tenor: log-linear on discount factors between "
                 "the published whole-year nodes, applied to every month_")
    if grid:
        head += (f"\n_{args.freq}ly sampling: {len(pts)} of {len(full)} published "
                 f"months shown_")
    foot = (f"\nStart {pct(full[0][1])} -> end {pct(full[-1][1])} "
            f"({bps(full[-1][1] - full[0][1])}). Low {pct(lo_p[1])} on {lo_p[0]}, "
            f"high {pct(hi_p[1])} on {hi_p[0]}"
            + (f", across all {len(full)} monthly observations." if grid else "."))
    if off_grid:
        foot += (f"\n\n_* {pts[-1][0]} is the latest release in range and does not "
                 f"fall on the {args.freq}ly grid; it is included so the series is "
                 f"not truncated. Its change column covers a partial period._")
    emit(args, {"country": code, "term": term, "curve_type": ctype,
                "frequency": args.freq,
                "range": {"first": full[0][0], "last": full[-1][0],
                          "published_months": len(full), "shown": len(pts)},
                "low": {"reference_date": lo_p[0], "spot_rate": lo_p[1]},
                "high": {"reference_date": hi_p[0], "spot_rate": hi_p[1]},
                "series": [{"reference_date": d, "spot_rate": v} for d, v in pts]},
         head + "\n\n" + table(["Reference date", "Spot rate", "Change"], out)
         + "\n" + foot)


def identical_groups(vals: dict) -> list:
    groups = {}
    for c, v in vals.items():
        if v is not None:
            groups.setdefault(round(v, 12), []).append(c)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def month_ranges(dates: list) -> str:
    """Compress a list of month-ends into readable spans: consecutive published
    months collapse, isolated ones stay separate."""
    if not dates:
        return ""
    spans, start, prev = [], dates[0], dates[0]
    for d in dates[1:]:
        gap = (int(d[:4]) * 12 + int(d[5:7])) - (int(prev[:4]) * 12 + int(prev[5:7]))
        if gap == 1:
            prev = d
            continue
        spans.append((start, prev))
        start = prev = d
    spans.append((start, prev))
    return ", ".join(a[:7] if a == b else f"{a[:7]} to {b[:7]}" for a, b in spans)


def identical_note(cv: Curves, vals: dict, ctype: str) -> str:
    """Flag curves that came back identical.

    EIOPA's risk-free curves are built per CURRENCY, not per sovereign, so every
    euro-area country shares one curve and a "German vs Italian spread" computed
    from this data is always zero. Readers expecting government bond yields find
    that baffling, and a table of identical numbers with no explanation looks
    like a bug in the tool rather than a property of the data.

    The list of country-specific volatility adjustments is derived from the pack
    rather than written out here, so a refreshed snapshot updates it for free.
    """
    groups = identical_groups(vals)
    if not groups:
        return ""
    shown = "; ".join(", ".join(g) for g in groups)
    tip = ""
    if ctype != "with_VA":
        exc = cv.va_exceptions()
        if exc:
            detail = "; ".join(f"{c} ({month_ranges(d)})" for c, d in exc.items())
            tip = (f" The with_VA curve (--va) can differ by country, but only "
                   f"where EIOPA set a country-specific volatility adjustment - "
                   f"across this snapshot that is {detail}, and nothing else.")
        else:
            tip = (" The with_VA curve (--va) never differs within a currency "
                   "group anywhere in this snapshot.")
    return ("\n\n_Identical by construction: " + shown + ". EIOPA publishes one "
            "risk-free curve per CURRENCY, not per sovereign, so countries sharing "
            "a currency share a curve. These are swap-derived risk-free rates - "
            "they carry no sovereign credit spread, so this data cannot show a "
            "BTP-Bund style spread." + tip + "_")


def cmd_compare(cv, args):
    codes, seen = [], set()
    for c in args.countries.split(","):
        if c.strip():
            r = resolve_country(c)
            if r not in seen:
                seen.add(r)
                codes.append(r)
    date, note = resolve_date(cv, args.date)
    k, ctype = ctype_id(args), ctype_name(args)
    term = int(args.term)
    base = resolve_country(args.vs) if args.vs else None

    vals = {c: cv.rate(c, date, k, term) for c in codes}
    if all(v is None for v in vals.values()):
        raise SystemExit(no_data(cv, codes[0], date, ctype))
    if base and vals.get(base) is None:
        raise SystemExit(f"No {term}y {ctype} rate for the base curve "
                         f"{label(base)} at {date}.")

    headers = ["Country", f"{term}y spot"] + ([f"vs {base}"] if base else [])
    rows = []
    for c in sorted(vals, key=lambda c: (vals[c] is None, -(vals[c] or 0))):
        row = [label(c), pct(vals[c])]
        if base:
            row.append("-" if c == base else
                       ("n/a" if vals[c] is None else bps(vals[c] - vals[base])))
        rows.append(row)
    head = f"**{term}y {ctype} spot rates at {date}**"
    if note:
        head += f"\n_{note}_"
    missing = [c for c in codes if vals[c] is None]
    foot = ("\n\n_No curve published at this date for: "
            + ", ".join(label(c) for c in missing) + "._") if missing else ""
    foot += identical_note(cv, vals, ctype)
    emit(args, {"reference_date": date, "curve_type": ctype, "term": term,
                "base": base, "rates": vals,
                "identical_groups": identical_groups(vals)},
         head + "\n\n" + table(headers, rows) + foot)


def cmd_forward(cv, args):
    """Forward rates are derived, not published: EIOPA's Term_Structures
    workbooks contain spot curves and their shocked variants, nothing else.
    EIOPA quotes spot rates with annual compounding, so the forward covering
    years t1..t2 is

        f = ((1+s2)^t2 / (1+s1)^t1) ^ (1/(t2-t1)) - 1

    Say so in any answer. Differing compounding conventions are the usual reason
    two people derive different forwards from the same published curve.
    """
    code = resolve_country(args.country)
    date, note = resolve_date(cv, args.date)
    k, ctype = ctype_id(args), ctype_name(args)
    curve = cv.curve(code, date, k)
    if not curve:
        raise SystemExit(no_data(cv, code, date, ctype))

    tenor = parse_terms(args.tenor)[0]
    rows, payload, interp = [], [], False
    for t1 in parse_terms(args.start):
        t2 = t1 + tenor
        if t1 == 0:
            s1, i1 = 0.0, False
        else:
            s1, i1 = cv.rate_at(code, date, k, t1)
        s2, i2 = cv.rate_at(code, date, k, t2)
        interp = interp or i1 or i2
        if s1 is None or s2 is None:
            rows.append([fmt_term(t1), fmt_term(tenor), pct(s1) if t1 else "-",
                         pct(s2), "n/a"])
            payload.append({"start": t1, "tenor": tenor, "forward_rate": None})
            continue
        f = ((1 + s2) ** t2 / (1 + s1) ** t1) ** (1.0 / (t2 - t1)) - 1
        rows.append([fmt_term(t1), fmt_term(tenor), "-" if t1 == 0 else pct(s1),
                     pct(s2), pct(f)])
        payload.append({"start": t1, "tenor": tenor, "spot_to_start": s1,
                        "spot_to_end": s2, "forward_rate": f})
    head = f"**{label(code)} - forwards implied by the {date} {ctype} curve**"
    if note:
        head += f"\n_{note}_"
    emit(args, {"country": code, "reference_date": date, "curve_type": ctype,
                "compounding": "annual", "forwards": payload},
         head + "\n\n"
         + table(["Starts in", "Tenor", "Spot to start", "Spot to end", "Forward"], rows)
         + interpolation_note(interp)
         + "\n\n_Derived from the published spot curve assuming annual compounding: "
           "f = ((1+s2)^t2 / (1+s1)^t1)^(1/(t2-t1)) - 1. EIOPA publishes spot curves "
           "only; these forwards appear in no EIOPA file._\n"
         + provenance(cv, code, date, ctype))


def cmd_shock(cv, args):
    """Reconstruct the Solvency II stressed term structures.

    EIOPA ships these as sheets of uncalculated formulas, so they are absent
    from every extracted dataset and read back as zeros if you open the workbook
    naively. They are, however, exactly reproducible - see `shocked_rate`.
    """
    code = resolve_country(args.country)
    date, note = resolve_date(cv, args.date)
    base_curve = cv.curve(code, date, 0)          # always built from no_VA
    if not base_curve:
        raise SystemExit(no_data(cv, code, date, "no_VA"))

    va = 0.0
    va_bp = None
    if getattr(args, "va", False):
        p = cv.params(code, date, "with_VA")
        if p is None or p.get("va") in (None, ""):
            raise SystemExit(f"No volatility adjustment published for {label(code)} "
                             f"at {date}, so the with_VA stress cannot be built.")
        va_bp = float(p["va"])
        va = va_bp / 10000.0

    if args.terms:
        terms = parse_terms(args.terms)
        # Refuse rather than round. The shock factors are defined per whole year,
        # so a fractional term has no factor; quietly snapping 7.5 to 7 would
        # hand back a stress for a different tenor than the one asked about.
        fractional = [t for t in terms if not isinstance(t, int)]
        if fractional:
            raise SystemExit(
                f"Shock factors are defined on whole years only, so "
                f"{', '.join(fmt_term(t) for t in fractional)} has no stress. "
                f"Ask for the bracketing whole years instead.")
    elif args.full:
        terms = sorted(base_curve)
    else:
        terms = [t for t in (1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50)
                 if t in base_curve]
    bad = [t for t in terms if t not in base_curve]
    if bad:
        raise SystemExit(f"Shocks are defined on published whole-year terms only; "
                         f"{', '.join(fmt_term(t) for t in bad)} is outside "
                         f"1-{max(base_curve)}y.")

    directions = ["down", "up"] if args.direction == "both" else [args.direction]
    want_df = getattr(args, "df", False)
    headers = ["Term", "Base"]
    for d in directions:
        headers.append(d.capitalize())
        if want_df:
            headers.append(f"DF {d}")
    rows, payload, floored = [], [], []
    for t in terms:
        s = base_curve[t]
        base = round(s + va, 5)
        row = [fmt_term(t), pct(base)]
        entry = {"term": t, "base": base}
        for d in directions:
            v = shocked_rate(s, t, d, va)
            row.append(pct(v))
            if want_df:
                row.append(f"{discount_factor(v, t):.6f}")
            entry[d] = v
            entry[f"{d}_factor"] = (SHOCK_DOWN if d == "down" else SHOCK_UP)[t - 1]
            if d == "up" and up_shock_floor_binds(s, t):
                floored.append(t)
        rows.append(row)
        payload.append(entry)

    ctype = "with_VA" if va else "no_VA"
    head = f"**{label(code)} - Solvency II interest-rate stress at {date} ({ctype})**"
    if note:
        head += f"\n_{note}_"
    foot = ("\n\n_Reconstructed, not published: EIOPA's shocked sheets hold "
            "uncalculated formulas and appear in no extracted dataset. Built from "
            "the no_VA curve with the Delegated Regulation maturity factors")
    if va:
        foot += (f", with the {va_bp:g} bp volatility adjustment added back after "
                 f"shocking (never by shocking the with_VA curve, which would "
                 f"understate the down stress)")
    foot += ".*_"
    foot = foot.replace(".*_", "._")
    if floored:
        foot += (f"\n\n_The one-percentage-point floor governs the up shock at "
                 f"{', '.join(fmt_term(t) for t in floored[:8])}"
                 f"{' and other tenors' if len(floored) > 8 else ''} - the "
                 f"proportional shock is smaller than 100 bp there._")
    neg = [t for t in terms if base_curve[t] < 0]
    if neg:
        foot += (f"\n\n_Negative base rates are left unshocked by the down stress "
                 f"at {', '.join(fmt_term(t) for t in neg[:8])}"
                 f"{' and others' if len(neg) > 8 else ''}._")
    foot += ("\n\n_An SCR calculation needs the whole stressed term structure, not "
             "one node: use --full._")
    emit(args, {"country": code, "reference_date": date, "curve_type": ctype,
                "va_bp": va_bp, "directions": directions, "reconstructed": True,
                "shocks": payload},
         head + "\n\n" + table(headers, rows) + foot
         + "\n" + provenance(cv, code, date, "no_VA"))


def cmd_params(cv, args):
    code = resolve_country(args.country)
    date, note = resolve_date(cv, args.date)
    ctype = ctype_name(args)
    d = cv.params(code, date, ctype)
    if d is None:
        raise SystemExit(f"No curve parameters for {label(code)} at {date} ({ctype}).")
    pretty = {"instrument_type": "Instrument", "coupon_freq": "Coupon frequency",
              "llp": "Last liquid point (years)",
              "convergence": "Convergence period (years)", "ufr": "UFR (%)",
              "alpha": "Alpha", "cra": "CRA (bp)", "va": "VA (bp)"}
    head = f"**{label(code)} - curve parameters at {date} ({ctype})**"
    if note:
        head += f"\n_{note}_"
    emit(args, d, head + "\n\n"
         + table(["Parameter", "Value"],
                 [[v, d[key]] for key, v in pretty.items() if key in d]))


def cmd_facts(cv, args):
    """Print the structural claims this skill relies on, derived from the pack.

    Every one of these was once a sentence in the documentation, and the
    documentation went stale the moment a new release landed. Deriving them
    means they cannot: refresh the snapshot and they refresh with it. When an
    answer turns on one of these facts, run this rather than quoting prose.
    """
    dates = cv.releases
    era = cv.currency_era_end()
    changes = cv.coverage_changes()
    exc = cv.va_exceptions()
    groups = cv.currency_groups(dates[-1])
    verified = all(cv.verify_group(g, dates[-1]) for g in groups)

    parts = [f"**Structural facts, derived from the bundled snapshot**\n",
             f"- {len(dates)} monthly releases, {dates[0]} to {dates[-1]}",
             f"- snapshot generated {cv.generated}",
             f"- {len(cv.countries)} distinct curves, {cv.n_observations:,} "
             f"spot observations"]
    if era:
        parts.append(f"- curves labelled by currency through {era}, by country "
                     f"from the next release")
    parts.append("")

    parts.append(f"**Shared curves at {dates[-1]}** — EIOPA publishes one curve "
                 f"per currency, so these are identical at every tenor"
                 + (" (verified over the full term structure)" if verified else "")
                 + ":\n")
    for g in groups:
        parts.append(f"- {', '.join(g)}")
    if not groups:
        parts.append("- none; every curve differs")
    parts.append("")

    parts.append("**Country-specific volatility adjustments** — the only places a "
                 "with_VA curve departs from its currency peers:\n")
    if exc:
        for c, ds in exc.items():
            parts.append(f"- {label(c)}: {len(ds)} months, {month_ranges(ds)}")
    else:
        parts.append("- none anywhere in this snapshot")
    parts.append("")

    parts.append(f"**Coverage changes** — {len(changes)} release(s) where the "
                 f"published set moved:\n")
    for d, added, removed in changes:
        bits = []
        if added:
            bits.append("added " + ", ".join(added))
        if removed:
            bits.append("dropped " + ", ".join(removed))
        parts.append(f"- {d}: {'; '.join(bits)}")
    parts.append("")
    parts.append("_These are computed on every run, not written down. If they "
                 "disagree with any documentation, trust these._")

    emit(args, {"releases": len(dates), "first": dates[0], "last": dates[-1],
                "snapshot_generated": cv.generated,
                "currency_era_end": era,
                "shared_curve_groups": groups,
                "shared_groups_verified": verified,
                "va_exceptions": exc,
                "coverage_changes": [{"date": d, "added": a, "removed": r}
                                     for d, a, r in changes]},
         "\n".join(parts))


def cmd_coverage(cv, args):
    dates = cv.releases
    if args.country:
        code = resolve_country(args.country)
        if not cv.has(code):
            raise SystemExit(f"{label(code)} does not appear anywhere in this snapshot.")
        rows = []
        for k, name in enumerate(CURVE_TYPES):
            ds = [d for d in dates if cv.curve_exists(code, k, d)]
            if ds:
                rows.append([name, ds[0], ds[-1], len(ds),
                             f"{cv.max_term(code, k, ds[-1])}y"])
        labs = cv.labels(code)
        gaps = [d for d in dates if d not in set(cv.dates_for(code))]
        extra = ""
        if len(labs) > 1:
            extra = "\n\n_Labelled in the source data as " + "; ".join(
                f"'{raw}' from {lo} to {hi}" for lo, hi, raw in labs) + "._"
        if gaps:
            extra += ("\n\n_Not published in " + ", ".join(gaps[:6])
                      + (f" and {len(gaps) - 6} other months" if len(gaps) > 6 else "")
                      + "._")
        emit(args, {"country": code, "labels": labs, "gaps": gaps},
             f"**Coverage for {label(code)}**\n\n"
             + table(["Curve", "From", "To", "Months", "Max term"], rows) + extra)
        return

    if args.date:
        date, _ = resolve_date(cv, args.date)
        cs = cv.countries_at(date)
        txt = f"**EIOPA RFR release {date}** - {len(cs)} curves\n\n" + ", ".join(cs)
        raws = [cv.raw_label(c, date) for c in cs]
        odd = [r for c, r in zip(cs, raws) if r != c]
        if odd:
            txt += ("\n\n_This release labels curves by currency in the source data ("
                    + ", ".join(odd[:6]) + " ...); they are normalised to country "
                    "codes here._")
        emit(args, {"reference_date": date, "countries": cs}, txt)
        return

    emit(args, {"dates": dates, "countries": cv.countries,
                "observations": cv.n_observations},
         f"**EIOPA RFR dataset (bundled snapshot)**\n\n"
         f"- {len(dates)} monthly releases, {dates[0]} to {dates[-1]}\n"
         f"- snapshot generated {cv.generated}; EIOPA publishes monthly, so any "
         f"release after {dates[-1]} is not in this data\n"
         f"- {len(cv.countries)} distinct curves, terms 1-150y, whole years only\n"
         f"- curve types: no_VA (default) and with_VA\n"
         f"- {cv.n_observations:,} spot observations\n\n"
         f"Countries: {', '.join(cv.countries)}\n\n"
         f"_Coverage changes over time - non-EEA curves were dropped from the "
         f"2025-01-31 release onward. Use `coverage --country XX` before assuming "
         f"a curve exists in a given month._")


# --------------------------------------------------------------------------

def main(argv=None):
    # SUPPRESS matters here: a subparser copies all of its defaults over the
    # namespace after the top-level parse, so a plain default would silently
    # undo `--va` given before the subcommand. With SUPPRESS an absent flag sets
    # no attribute at all, and the getattr fallbacks decide.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pack", type=Path, default=argparse.SUPPRESS,
                        help="use a different curve pack (default: the bundled one)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="raw JSON; rates as decimals")
    common.add_argument("--va", action="store_true", default=argparse.SUPPRESS,
                        help="use the with_VA curve instead of the default no_VA")
    common.add_argument("--df", action="store_true", default=argparse.SUPPRESS,
                        help="also show discount factors, (1+r)^-t")

    p = argparse.ArgumentParser(
        description="Query the EIOPA risk-free rate history (self-contained).",
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage examples:")[-1])
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))

    s = sub.add_parser("spot", help="spot rate(s) for one country and date")
    s.add_argument("--country", required=True)
    s.add_argument("--term", required=True, help="years, e.g. 10 or 1,5,10")
    s.add_argument("--date", required=True)
    s.set_defaults(fn=cmd_spot)

    s = sub.add_parser("curve", help="the term structure for one country and date")
    s.add_argument("--country", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--terms", help="comma list; default is the key tenors")
    s.add_argument("--full", action="store_true", help="every published term")
    s.set_defaults(fn=cmd_curve)

    s = sub.add_parser("series", help="one tenor through time")
    s.add_argument("--country", required=True)
    s.add_argument("--term", required=True)
    s.add_argument("--from", dest="frm", help="start of range, e.g. 2020 or 2020-06")
    s.add_argument("--to", help="end of range")
    s.add_argument("--freq", choices=["month", "quarter", "year"], default="month")
    s.set_defaults(fn=cmd_series)

    s = sub.add_parser("compare", help="one tenor across countries at one date")
    s.add_argument("--countries", required=True, help="comma list")
    s.add_argument("--term", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--vs", help="base country, to show spreads in basis points")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("forward", help="forward rates implied by one curve")
    s.add_argument("--country", required=True)
    s.add_argument("--start", required=True,
                   help="years until the forward starts, e.g. 5 or 1,5,10")
    s.add_argument("--tenor", required=True, help="length of the forward period, years")
    s.add_argument("--date", required=True)
    s.set_defaults(fn=cmd_forward)

    s = sub.add_parser("shock", help="reconstruct the Solvency II stressed curves")
    s.add_argument("--country", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--direction", choices=["down", "up", "both"], default="both")
    s.add_argument("--terms", help="comma list of whole years; default key tenors")
    s.add_argument("--full", action="store_true", help="every published term")
    s.set_defaults(fn=cmd_shock)

    s = sub.add_parser("params", help="UFR, LLP, alpha, CRA and VA for one curve")
    s.add_argument("--country", required=True)
    s.add_argument("--date", required=True)
    s.set_defaults(fn=cmd_params)

    sub.add_parser("facts", help="the structural claims this skill relies on, "
                                 "derived from the data").set_defaults(fn=cmd_facts)

    s = sub.add_parser("coverage", help="what this snapshot actually contains")
    s.add_argument("--date", help="list the curves in one release")
    s.add_argument("--country", help="show one country's history and labels")
    s.set_defaults(fn=cmd_coverage)

    s = sub.add_parser("pack", help="rebuild the snapshot from an EIOPA_all_curves "
                                    "checkout (maintenance only)")
    s.add_argument("--data-dir", type=Path, required=True,
                   help="folder holding yield_curves.csv")
    s.add_argument("--out", type=Path, default=None,
                   help=f"destination (default: {bundled_pack()})")
    s.set_defaults(fn=None)

    args = p.parse_args(argv)
    if args.cmd == "pack":
        write_pack(args.data_dir, args.out or bundled_pack())
        return 0

    args._cv = load(getattr(args, "pack", None))
    args.fn(args._cv, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
