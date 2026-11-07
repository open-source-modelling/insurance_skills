#!/usr/bin/env python3
"""
Ask several LLMs to rank Australian super funds by fees for a customer (persona)
and compare their answers with the fees in inputs/fees.csv.

For every persona in personas/*.txt, every model and every run, the prompt is the
persona text + the funds in inputs/companies.csv + the answer-format rules below. The
model's | Rank | Fund | Admin | Investment | Transaction | Total | Reason | table is parsed per fund.

The persona's balance is the first dollar amount in its text ($50,000 if none). Actual fees and costs,
with the rates in inputs/fees.csv as decimals, the way Excel stores percentages (0.50% p.a. = 0.005):
    admin  = admin_fee_fixed_aud + balance * admin_fee_rate
    invest = balance * investment_fee_rate
    trans  = balance * transaction_cost_rate
    total  = admin + invest + trans        (funds are ranked by total)

Outputs in results/<timestamp>/<persona>/:
    summary.csv    one row per fund: actual rank and fees, each model's mean rank and fees
    accuracy.csv   one row per model: rank correlation with the actual ranking, median % error per fee,
                   and how many of its totals don't equal its own admin + investment + transaction
    agreement.csv  rank correlation between every pair of rankings: the actual one and each model's
    long.csv       one row per run x model x fund, with the model's reason and total_gap
    raw.json       prompt, full responses, tokens and cost

Setup:  pip install -r requirements.txt
        set ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, OPENAI_API_KEY, GEMINI_API_KEY
Run:    python llm_super_ranking.py [--runs 5] [--provider anthropic]
"""
import argparse
import difflib
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"  # companies.csv and fees.csv

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


def read_csv(name: str, **kw: Any) -> pd.DataFrame:
    """Read a CSV file from the inputs folder.

    Reads Excel's "CSV UTF-8" files, with or without a byte-order mark. Bytes that
    are not valid UTF-8, such as the non-breaking spaces in a file saved in Windows
    encoding, are dropped.

    Args:
        name: File name in the inputs folder, e.g. "fees.csv".
        **kw: Passed on to pandas.read_csv, e.g. index_col="company".

    Returns:
        The file as a DataFrame.
    """
    return pd.read_csv(INPUTS / name, encoding="utf-8-sig", encoding_errors="ignore", **kw)


def norm(name: str) -> str:
    """Reduce a fund name to lower-case letters and digits, for loose matching.

    Example: "**HOSTPLUS Superannuation Fund**" -> "hostplussuperannuationfund".

    Args:
        name: A fund name as written in companies.csv or by a model.

    Returns:
        The name without case, spaces, punctuation or markdown.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


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


def parse(text: str, funds: list[str]) -> dict[str, dict[str, Any]]:
    """Read the model's markdown table into one record per fund.

    A line counts as a row if it has at least 7 cells and a number in the first,
    which skips prose, the header and the separator row. The fund cell is matched
    to the closest name in `funds` (ignoring case, spaces and punctuation; at least
    80% similar). Rows that match no fund are skipped, and if a fund appears twice
    only its first row is kept.

    Args:
        text: The model's full answer.
        funds: The fund names as listed in the prompt.

    Returns:
        {fund: {"rank", "est_admin", "est_invest", "est_trans", "est_total", "reason"}}.
        Fee estimates are floats, or None when a cell has no number.
    """
    by_norm = {norm(f): f for f in funds}
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rank = re.search(r"\d+", cells[0]) if len(cells) >= 7 else None
        if not rank:
            continue  # not a table row, or the header / separator row
        hit = difflib.get_close_matches(norm(cells[1]), by_norm, n=1, cutoff=0.8)
        if hit and by_norm[hit[0]] not in out:
            out[by_norm[hit[0]]] = {"rank": int(rank.group()), "est_admin": dollars(cells[2]),
                                   "est_invest": dollars(cells[3]), "est_trans": dollars(cells[4]),
                                   "est_total": dollars(cells[5]), "reason": cells[6]}
    return out


def main() -> None:
    """Ask every selected model about every persona and write the results.

    Command-line options: --runs N (times to ask each model, default 1) and
    --provider {all,anthropic,openai,google}. For each persona in personas/, writes
    raw.json, long.csv, summary.csv, accuracy.csv and agreement.csv to results/<timestamp>/<persona>/
    (see the module docstring) and prints the summary and accuracy tables. A failed
    call is printed and recorded, and the run carries on.

    Raises:
        KeyError: A fund in companies.csv is missing from fees.csv. This is raised
            before any API call is made.
    """
    p = argparse.ArgumentParser(description="Rank Australian super funds by fees with several LLMs.")
    p.add_argument("--runs", type=int, default=1, help="times to ask each model (default 1)")
    p.add_argument("--provider", choices=["all", *CALLERS], default="all")
    args = p.parse_args()

    funds = read_csv("companies.csv").company.str.strip().drop_duplicates().tolist()
    fees = read_csv("fees.csv", index_col="company").loc[funds]  # KeyError here = fund missing from fees.csv
    models = [m for m in MODELS if args.provider in ("all", m["provider"])]
    run_dir = ROOT / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total_cost = 0.0

    for persona in sorted((ROOT / "personas").glob("*.txt")):
        persona_text = persona.read_text(encoding="utf-8").strip()
        m = re.search(r"\$([\d,]+)", persona_text)
        balance = float(m.group(1).replace(",", "")) if m else 50_000.0
        admin = fees.admin_fee_fixed_aud + balance * fees.admin_fee_rate
        invest = balance * fees.investment_fee_rate
        trans = balance * fees.transaction_cost_rate
        total = admin + invest + trans
        actual = pd.DataFrame({"actual_rank": total.rank(method="min").astype(int), "actual_total": total,
                               "actual_admin": admin, "actual_invest": invest, "actual_trans": trans}).round(2)
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
                parsed = parse(answer, funds)
                print(error or f"ranked {len(parsed)}/{len(funds)}, ${cost:.4f}")
                calls.append({"model": cfg["model"], "run": run, "error": error, "input_tokens": tin,
                              "output_tokens": tout, "cost_usd": cost, "response": answer})
                rows += [{"run": run, "model": cfg["model"], "fund": f, **v} for f, v in parsed.items()]

        out = run_dir / persona.stem
        out.mkdir(parents=True)
        (out / "raw.json").write_text(json.dumps({"balance": balance, "prompt": prompt, "calls": calls},
                                                 indent=2, ensure_ascii=False), encoding="utf-8")
        if not rows:
            continue
        df = pd.DataFrame(rows).join(actual, on="fund")
        for part in PARTS:
            est, act = df[f"est_{part}"], df[f"actual_{part}"]
            df[f"abs_pct_err_{part}"] = ((est - act).abs() / act * 100).round(1)
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
