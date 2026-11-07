#!/usr/bin/env python3
"""
Ask several LLMs to rank Australian super funds by fees for a customer (persona)
and compare their answers with the fees in inputs/fees.csv.

For every persona in personas/*.txt, every model and every run, the prompt is the
persona text + the funds in inputs/companies.csv + the answer-format rules below. The
model's | Rank | Fund | Admin | Investment | Transaction | Total | Reason | table is parsed per fund.

Each persona file starts with a header giving the member's balance (see read_persona). Actual fees and
costs, with the rates in inputs/fees.csv as decimals, the way Excel stores percentages (0.50% p.a. = 0.005):
    admin  = admin_fee_fixed_aud + balance * admin_fee_rate
    invest = balance * investment_fee_rate
    trans  = balance * transaction_cost_rate
    total  = admin + invest + trans        (funds are ranked by total)

Every input is checked before the first API call, so a mistake in a file costs nothing.

Outputs in results/<timestamp>/<persona>/:
    summary.csv    one row per fund: actual rank and fees, each model's mean rank and fees
    accuracy.csv   one row per model: rank correlation with the actual ranking, median % error per fee,
                   and how many of its totals don't equal its own admin + investment + transaction
    agreement.csv  rank correlation between every pair of rankings: the actual one and each model's
    long.csv       one row per run x model x fund, with the model's reason and total_gap
    raw.json       prompt, full responses, table rows that could not be used, tokens and cost

Setup:  pip install -r requirements.txt
        set ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, OPENAI_API_KEY, GEMINI_API_KEY
Run:    python llm_super_ranking.py [--runs 5] [--provider anthropic] [--output-dir DIR]
Test:   python -m unittest discover tests      (offline: no API calls)
"""
import argparse
import difflib
import json
import os
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"  # companies.csv and fees.csv
PERSONAS = ROOT / "personas"  # one .txt file per member
RESULTS = ROOT / "results"  # default --output-dir

# Prices in USD per 1M tokens; re-check before relying on the cost figures.
MODELS: list[dict[str, Any]] = [
    {"provider": "anthropic", "model": "claude-sonnet-5", "in": 2.00, "out": 10.00, "max_tokens": 16384},
    {"provider": "openai", "model": "gpt-5.6-terra", "in": 2.00, "out": 12.00, "max_tokens": 32768},
    {"provider": "google", "model": "gemini-3.1-pro-preview", "in": 2.00, "out": 12.00, "max_tokens": 32768},
]

# The investment option the fees in inputs/fees.csv are for; the prompt asks the models about it.
OPTION = "the fund's growth or high-growth investment option (for a lifecycle product, the stage for your age)"

FORMAT = """

Funds:
{funds}

Answer with a single markdown table with exactly these columns:
| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |

Rules:
- Rank 1 has the lowest total annual fees and costs for you, rank {n} the highest.
- The dollar columns are your estimates for one year in {option} on your balance, each as a single dollar amount: administration fees, investment fees, transaction costs, and their total.
- Include every fund exactly once, and no other funds.
- Write each fund name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the fund this rank.
"""


CallResult = tuple[str, int, int]  # (answer text, input tokens, output tokens)


def call_anthropic(model: str, prompt: str, max_tokens: int) -> CallResult:
    """Send the prompt to an Anthropic model as a single user message.

    Needs ANTHROPIC_API_KEY and ANTHROPIC_WORKSPACE_ID in the environment.

    Args:
        model: Anthropic model id, e.g. "claude-sonnet-5".
        prompt: The full prompt.
        max_tokens: Cap on output tokens.

    Returns:
        The answer text (text blocks only), input tokens and output tokens.
    """
    import anthropic
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]})
    r = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text"), r.usage.input_tokens, r.usage.output_tokens


def call_openai(model: str, prompt: str, max_tokens: int) -> CallResult:
    """Send the prompt to an OpenAI model through the Responses API.

    Needs OPENAI_API_KEY in the environment.

    Args:
        model: OpenAI model id, e.g. "gpt-5.6-terra".
        prompt: The full prompt.
        max_tokens: Cap on output tokens.

    Returns:
        The answer text, input tokens and output tokens (including reasoning tokens).
    """
    from openai import OpenAI
    r = OpenAI().responses.create(model=model, input=prompt, max_output_tokens=max_tokens)
    return r.output_text, r.usage.input_tokens, r.usage.output_tokens


def call_google(model: str, prompt: str, max_tokens: int) -> CallResult:
    """Send the prompt to a Gemini model.

    Needs GEMINI_API_KEY in the environment.

    Args:
        model: Gemini model id, e.g. "gemini-3.1-pro-preview".
        prompt: The full prompt.
        max_tokens: Cap on output tokens.

    Returns:
        The answer text, input tokens and output tokens (answer plus thinking,
        since both are billed as output).
    """
    from google import genai
    client = genai.Client()  # keep a reference: a discarded Client closes its connection before the request is sent
    r = client.models.generate_content(model=model, contents=prompt, config={"max_output_tokens": max_tokens})
    u = r.usage_metadata  # thinking tokens are billed as output
    return r.text or "", u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)


CALLERS: dict[str, Callable[[str, str, int], CallResult]] = {
    "anthropic": call_anthropic, "openai": call_openai, "google": call_google}
PARTS = ["total", "admin", "invest", "trans"]  # fee columns, in output order
TOTAL_TOLERANCE = 5  # AUD; a model's total may differ from the sum of its parts by this much (rounding)


def read_csv(path: Path, **kw: Any) -> pd.DataFrame:
    """Read an input CSV file.

    The file must be UTF-8, with or without a byte-order mark: what Excel writes
    with "CSV UTF-8". Any other encoding is refused rather than guessed, because a
    dropped or misread character can quietly change a fund's name.

    Args:
        path: The file, e.g. INPUTS / "fees.csv".
        **kw: Passed on to pandas.read_csv, e.g. dtype=str.

    Returns:
        The file as a DataFrame.

    Raises:
        SystemExit: The file is not valid UTF-8.
    """
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kw)
    except UnicodeDecodeError:
        raise SystemExit(f"{path} is not UTF-8. In Excel, save it as 'CSV UTF-8'.") from None


GENERIC = {"super", "superannuation", "fund", "mysuper", "the", "of", "and"}  # words that don't tell funds apart
SIMILARITY = 0.8  # how alike a misspelt name must be to the real one (difflib's ratio)


def norm(name: str) -> str:
    """Reduce a fund name to lower-case words of letters and digits, for loose matching.

    Accents, markdown, punctuation and anything in brackets are dropped. Example:
    "**HOSTPLUS Superannuation Fund** (High Growth)" -> "hostplus superannuation fund".

    Args:
        name: A fund name as written in companies.csv or by a model.

    Returns:
        The name as space-separated lower-case words.
    """
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name).split())


def similar(a: str, b: str) -> bool:
    """True if two strings are at least SIMILARITY alike (difflib's ratio)."""
    return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY


class FundNames:
    """The funds to rank, with their aliases, and the rules for matching a model's spelling to one."""

    def __init__(self, funds: dict[str, list[str]]):
        """
        Args:
            funds: {fund name as given to the models: [aliases]}, in prompt order.

        Raises:
            SystemExit: Two funds share a name or alias once case, spaces and
                punctuation are ignored.
        """
        self.funds = list(funds)
        self.entries: list[tuple[str, frozenset[str], str]] = []  # (name without spaces, its words, fund)
        owner: dict[str, str] = {}
        for fund, aliases in funds.items():
            for name in [fund, *aliases]:
                key = norm(name)
                if not key:
                    continue
                compact = key.replace(" ", "")
                other = owner.setdefault(compact, fund)
                if other != fund:
                    raise SystemExit(f"companies.csv: {name!r}, a name for {fund}, is also a name for {other}")
                self.entries.append((compact, frozenset(key.split()), fund))

    def match(self, cell: str) -> str | None:
        """Map a fund name written by a model to the fund's name in companies.csv.

        Case, accents, spaces, punctuation, markdown and anything in brackets are
        ignored. The first rule that gives exactly one fund wins:

        1. Exact: the name, or one of the aliases, of a fund.
        2. Shortened: every word of `cell` is in exactly one fund's name or alias,
           e.g. "HOSTPLUS" -> "HOSTPLUS Superannuation Fund".
        3. Misspelt: at least SIMILARITY alike to a fund's name or alias, and sharing
           a distinctive word with it (one not in GENERIC), so "Aware Super" does not
           become "REI Super".

        A name made only of GENERIC words, such as "Super Fund", matches nothing.

        Args:
            cell: The fund cell of a table row.

        Returns:
            The fund's name in companies.csv, or None if no rule gives exactly one fund.
        """
        key = norm(cell)
        compact = key.replace(" ", "")
        if not compact:
            return None
        for name, _, fund in self.entries:
            if name == compact:
                return fund
        words = set(key.split())
        distinctive = words - GENERIC
        if not distinctive:
            return None
        shortened = {fund for _, name_words, fund in self.entries if words <= name_words}
        if shortened:
            return shortened.pop() if len(shortened) == 1 else None
        misspelt = {fund for name, name_words, fund in self.entries
                    if similar(compact, name)
                    and any(similar(w, v) for w in distinctive for v in name_words - GENERIC)}
        return misspelt.pop() if len(misspelt) == 1 else None


def load_funds(path: Path = INPUTS / "companies.csv") -> FundNames:
    """Read the funds to rank from companies.csv.

    Columns: company, the name the models are given, and optionally aliases: other
    names a model may use for the same fund, separated by semicolons, e.g.
    "Aware Super" for MySuper Lifecycle. Funds keep the file's order; a repeated name
    is dropped.

    Args:
        path: The companies file.

    Returns:
        The funds and their aliases.

    Raises:
        SystemExit: The file is not UTF-8, has no company column or lists no funds,
            or two funds share a name or alias.
    """
    df = read_csv(path, dtype=str, keep_default_na=False)
    if "company" not in df:
        raise SystemExit(f"{path} needs a 'company' column")
    funds: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        name = row["company"].strip()
        if name and name not in funds:
            funds[name] = [a.strip() for a in row.get("aliases", "").split(";") if a.strip()]
    if not funds:
        raise SystemExit(f"{path} lists no funds")
    return FundNames(funds)


def dollars(cell: str) -> float | None:
    """Read a dollar amount from a table cell.

    Takes the first number in the cell, ignoring "$" and thousands separators, so
    a range counts as its lower end. Examples: "$1,234" -> 1234.0,
    "~$300-400" -> 300.0, "unknown" -> None.

    Args:
        cell: The text of one table cell.

    Returns:
        The amount, or None if the cell contains no number.
    """
    m = re.search(r"\d[\d,]*(\.\d+)?", cell)
    return float(m.group().replace(",", "")) if m else None


def parse(text: str, fund_names: FundNames) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read the model's markdown table into one record per fund.

    A line counts as a row if it has at least 7 cells and a number in the first,
    which skips prose, the header and the separator row. The fund cell is matched
    with FundNames.match. If a fund appears twice, only its first row is kept.

    Args:
        text: The model's full answer.
        fund_names: The funds as listed in the prompt, with their aliases.

    Returns:
        {fund: {"rank", "est_admin", "est_invest", "est_trans", "est_total", "reason"}},
        and the table rows that could not be used: rows whose fund matches no fund, and
        rows that start like a table row but have fewer than 7 cells, such as a row cut
        off at the end of the answer. Fee estimates are floats, or None when a cell has
        no number.
    """
    out, unmatched = {}, []
    for line in text.splitlines():
        line = line.strip()
        cells = [c.strip() for c in line.strip("|").split("|")]
        rank = re.search(r"\d+", cells[0])
        if not rank:
            continue  # prose, the header or the separator row
        if len(cells) < 7:
            if line.startswith("|"):
                unmatched.append(line)  # a table row cut short
            continue
        fund = fund_names.match(cells[1])
        if fund is None:
            unmatched.append(line)
        elif fund not in out:
            out[fund] = {"rank": int(rank.group()), "est_admin": dollars(cells[2]),
                         "est_invest": dollars(cells[3]), "est_trans": dollars(cells[4]),
                         "est_total": dollars(cells[5]), "reason": cells[6]}
    return out, unmatched


def read_persona(path: Path) -> tuple[float, str]:
    """Read a persona file: a header with the member's balance, a line "---", then the text for the models.

        balance: 150000
        ---
        You are a 36-year-old Australian with $150,000 in super. ...

    The balance is what the actual fees are calculated on. The text must state the
    same balance, written as 150,000 or 150000, so that the models are asked about it.

    Args:
        path: The persona file.

    Returns:
        The balance, and the text after the header (which is all the models see).

    Raises:
        SystemExit: The file is not UTF-8, the header is missing or malformed, or the
            text does not state the balance.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise SystemExit(f"{path} is not UTF-8") from None
    header, sep, text = raw.partition("\n---\n")
    text = text.strip()
    if not sep or not text:
        raise SystemExit(f"{path}: start the file with 'balance: <amount>', then a line '---', "
                         "then the text for the models")
    fields = {}
    for line in header.strip().splitlines():
        key, colon, value = line.partition(":")
        if not colon:
            raise SystemExit(f"{path}: header line {line!r} is not 'name: value'")
        fields[key.strip().lower()] = value.strip()
    unknown = sorted(set(fields) - {"balance"})
    if unknown:
        raise SystemExit(f"{path}: unknown header field(s) {', '.join(unknown)}; only 'balance' is used")
    try:
        balance = float(re.sub(r"[$,\s]", "", fields["balance"]))
    except (KeyError, ValueError):
        raise SystemExit(f"{path}: the header needs the balance in dollars, e.g. 'balance: 150000'") from None
    if not balance > 0:
        raise SystemExit(f"{path}: the balance must be above 0")
    stated = (rf"(?<![\d.,]){re.escape(s)}(?![.,]?\d)" for s in (f"{balance:,.0f}", f"{balance:.0f}"))
    if not any(re.search(pattern, text) for pattern in stated):
        raise SystemExit(f"{path}: the header gives a balance of ${balance:,.0f}, but the text does not "
                         "state it. The models must be asked about the balance the actual fees use.")
    return balance, text


FEE_COLUMNS = ["admin_fee_fixed_aud", "admin_fee_rate", "investment_fee_rate", "transaction_cost_rate"]
MAX_RATE = 0.05  # 5% a year; a higher rate is almost certainly a percentage typed as a number


def load_fees(funds: list[str], path: Path = INPUTS / "fees.csv") -> pd.DataFrame:
    """Read the actual fees of `funds` from fees.csv, and check them.

    Args:
        funds: The fund names from companies.csv.
        path: The fees file.

    Returns:
        One row per fund, in the order of `funds`, indexed by name, with the
        FEE_COLUMNS as numbers.

    Raises:
        SystemExit: Listing every problem found: a fund that is missing or appears
            twice, a fee that is not a number or is negative, or a rate above MAX_RATE.
    """
    fees = read_csv(path, dtype=str, keep_default_na=False)
    if "company" not in fees:
        raise SystemExit(f"{path} needs a 'company' column")
    fees["company"] = fees["company"].str.strip()
    problems = [f"{c} appears more than once" for c in fees["company"][fees["company"].duplicated()].unique()]
    fees = fees.drop_duplicates("company").set_index("company")
    problems += [f"{f} is in companies.csv but not in fees.csv" for f in funds if f not in fees.index]
    for col in FEE_COLUMNS:
        if col not in fees:
            problems.append(f"the column {col} is missing")
            continue
        text = fees[col].str.strip()
        values = pd.to_numeric(text, errors="coerce")
        for fund in fees.index:
            raw, value = text[fund], values[fund]
            if pd.isna(value):
                hint = "; rates are decimals, so 0.50% is 0.005" if raw.endswith("%") else ""
                problems.append(f"{fund}: {col} is {raw!r}, not a number{hint}")
            elif value < 0:
                problems.append(f"{fund}: {col} is negative ({raw})")
            elif col != "admin_fee_fixed_aud" and value > MAX_RATE:
                problems.append(f"{fund}: {col} is {raw}, above {MAX_RATE:.0%} a year; "
                                "rates are decimals, so 0.50% is 0.005")
        fees[col] = values
    if problems:
        raise SystemExit(f"{path} has {len(problems)} problem(s):\n- " + "\n- ".join(problems))
    return fees.loc[funds]


def actual_fees(fees: pd.DataFrame, balance: float) -> pd.DataFrame:
    """Each fund's actual annual fees and costs on `balance`, and its rank by total.

    Args:
        fees: The fees, as returned by load_fees.
        balance: The member's balance in dollars.

    Returns:
        One row per fund: actual_rank (1 = cheapest; equal totals share the better
        rank), actual_total, actual_admin, actual_invest and actual_trans, in dollars
        rounded to cents.
    """
    admin = fees.admin_fee_fixed_aud + balance * fees.admin_fee_rate
    invest = balance * fees.investment_fee_rate
    trans = balance * fees.transaction_cost_rate
    total = admin + invest + trans
    return pd.DataFrame({"actual_rank": total.rank(method="min").astype(int), "actual_total": total,
                         "actual_admin": admin, "actual_invest": invest, "actual_trans": trans}).round(2)


def abs_pct_err(est: pd.Series, act: pd.Series) -> pd.Series:
    """|estimate - actual| / actual in %, rounded to 0.1. Blank (NaN) where the actual
    amount is 0, such as a fund with no transaction costs: no % error exists there."""
    return ((est - act).abs() / act.where(act != 0) * 100).round(1)


def main(argv: list[str] | None = None) -> None:
    """Ask every selected model about every persona and write the results.

    Command-line options: --runs N (times to ask each model, default 1),
    --provider {all,anthropic,openai,google} and --output-dir DIR (default results/).
    For each persona in personas/, writes raw.json, long.csv, summary.csv, accuracy.csv
    and agreement.csv to <output-dir>/<timestamp>/<persona>/ (see the module docstring)
    and prints the summary and accuracy tables. A failed call is printed and recorded,
    and the run carries on.

    Args:
        argv: The command-line arguments; None means sys.argv.

    Raises:
        SystemExit: An input file has a problem (see load_funds, load_fees and
            read_persona). Every input is checked before the first API call.
    """
    p = argparse.ArgumentParser(description="Rank Australian super funds by fees with several LLMs.")
    p.add_argument("--runs", type=int, default=1, help="times to ask each model (default 1)")
    p.add_argument("--provider", choices=["all", *CALLERS], default="all")
    p.add_argument("--output-dir", type=Path, default=RESULTS,
                   help="folder for results; each invocation gets a timestamped subfolder (default results/)")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs must be at least 1")

    # Check every input before the first (paid) API call.
    fund_names = load_funds()
    funds = fund_names.funds
    fees = load_fees(funds)
    persona_files = sorted(PERSONAS.glob("*.txt"))
    if not persona_files:
        raise SystemExit(f"No persona files (*.txt) in {PERSONAS}")
    personas = [(path, *read_persona(path)) for path in persona_files]

    models = [m for m in MODELS if args.provider in ("all", m["provider"])]
    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total_cost = 0.0

    for persona, balance, persona_text in personas:
        actual = actual_fees(fees, balance)
        prompt = persona_text + FORMAT.format(funds="\n".join(f"- {f}" for f in funds), n=len(funds), option=OPTION)
        print(f"\n=== {persona.stem}: balance ${balance:,.0f} ===")

        rows, calls = [], []
        for cfg in models:
            for run in range(1, args.runs + 1):
                print(f"{cfg['model']} run {run} ... ", end="", flush=True)
                try:
                    answer, tin, tout = CALLERS[cfg["provider"]](cfg["model"], prompt, cfg["max_tokens"])
                    error = ""
                except Exception as e:  # keep going if one call fails
                    answer, tin, tout, error = "", 0, 0, f"{type(e).__name__}: {e}"
                cost = (tin * cfg["in"] + tout * cfg["out"]) / 1e6
                total_cost += cost
                parsed, unmatched = parse(answer, fund_names)
                print(error or f"ranked {len(parsed)}/{len(funds)}, ${cost:.4f}")
                if unmatched:
                    print(f"    {len(unmatched)} table row(s) not used: fund not recognised or row cut off (see raw.json)")
                calls.append({"model": cfg["model"], "run": run, "error": error, "input_tokens": tin,
                              "output_tokens": tout, "cost_usd": cost, "response": answer,
                              "unmatched_rows": unmatched})
                rows += [{"run": run, "model": cfg["model"], "fund": f, **v} for f, v in parsed.items()]

        out = run_dir / persona.stem
        out.mkdir(parents=True)
        (out / "raw.json").write_text(json.dumps({"balance": balance, "prompt": prompt, "calls": calls},
                                                 indent=2, ensure_ascii=False), encoding="utf-8")
        if not rows:
            continue
        df = pd.DataFrame(rows).join(actual, on="fund")
        for part in PARTS:
            df[f"abs_pct_err_{part}"] = abs_pct_err(df[f"est_{part}"], df[f"actual_{part}"])
        df["total_gap"] = (df.est_total - df.est_admin - df.est_invest - df.est_trans).round(2)  # model's total minus its own parts
        long_cols = ["run", "model", "fund", "actual_rank", "rank"]
        for part in PARTS:  # actual, estimate and % error side by side for each fee
            long_cols += [f"actual_{part}", f"est_{part}", f"abs_pct_err_{part}"]
        df[long_cols + ["total_gap", "reason"]].to_csv(out / "long.csv", index=False, encoding="utf-8-sig")

        cols = ["rank", *(f"est_{part}" for part in PARTS)]
        means = df.pivot_table(index="fund", columns="model", values=cols)[cols].round(1)
        means.columns = ["_".join(col) for col in means.columns]  # ("rank", model) -> "rank_<model>"
        summary = actual.join(means).sort_values("actual_rank")
        names = [cfg["model"] for cfg in models]
        order = ["actual_rank", *(f"rank_{n}" for n in names)]
        for part in PARTS:  # actual first, then each model in MODELS order
            order += [f"actual_{part}", *(f"est_{part}_{n}" for n in names)]
        summary = summary[[c for c in order if c in summary]]  # skips models with no answers
        summary.to_csv(out / "summary.csv", index_label="fund", encoding="utf-8-sig")

        # Spearman rank correlation between the actual ranking and each model's, and between models
        labels = {"actual_rank": "actual", **{f"rank_{n}": n for n in names}}
        agreement = summary[[c for c in labels if c in summary]].rename(columns=labels).corr(method="spearman").round(2)
        agreement.to_csv(out / "agreement.csv", index_label="ranking", encoding="utf-8-sig")

        by_model = df.groupby("model")
        accuracy = pd.DataFrame({
            "rank_corr": {m: g["rank"].corr(g.actual_rank) for m, g in by_model},  # Spearman when no ties
            **{f"median_abs_pct_err_{part}": by_model[f"abs_pct_err_{part}"].median() for part in PARTS},
            "funds_ranked_per_run": by_model.size() / args.runs,
            "total_mismatches_per_run": (df.total_gap.abs() > TOTAL_TOLERANCE).groupby(df.model).sum() / args.runs,
        }).round(2)
        accuracy.to_csv(out / "accuracy.csv", index_label="model", encoding="utf-8-sig")
        print(f"\n{summary.to_string()}\n\n{accuracy.to_string()}\n\n{agreement.to_string()}\nSaved to {out}")

    print(f"\nTotal cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
