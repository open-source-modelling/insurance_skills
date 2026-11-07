#!/usr/bin/env python3
"""
Ask several LLMs to rank Italian life insurers from the point of view of a
customer (a "persona") and record what each model says about each company.

The script loops over every persona in personas/ (or those given with
--persona). For each persona, every model gets the same prompt: the persona
text, the list of companies from companies.csv, and fixed answer-format rules.
Each model's markdown table is parsed back into a rank and a reason per
company. Results are written to results/<timestamp>/<persona>/:

    rankings.csv   company | <model 1> | <model 2> | ... | avg_rank
    reasons.csv    same shape, each cell holds the model's reason
    long.csv       one row per run x model x company (rank + reason)
    report.md      both tables plus the prompt, cost and any issues
    raw.json       full responses, token counts and cost per call

Setup:
    pip install -r requirements.txt

    Environment variables: ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID,
    OPENAI_API_KEY, GEMINI_API_KEY

Run:
    python llm_life_insurer_ranking.py
    python llm_life_insurer_ranking.py --provider anthropic
    python llm_life_insurer_ranking.py --runs 5
    python llm_life_insurer_ranking.py --persona personas/young_family.txt
"""

import argparse
import csv
import difflib
import json
import os
import re
import statistics
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPANIES_PATH = ROOT / "companies.csv"
PERSONAS_DIR = ROOT / "personas"
OUTPUT_DIR = ROOT / "results"

# Prices are USD per 1M tokens, standard tier. Re-check before relying on cost:
#   https://docs.claude.com/en/docs/about-claude/pricing
#   https://developers.openai.com/api/docs/pricing
#   https://ai.google.dev/gemini-api/docs/pricing
MODELS = [
    {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "input_price_per_m": 2.00,
        "output_price_per_m": 10.00,
    },
    {
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "input_price_per_m": 2.00,
        "output_price_per_m": 12.00,
    },
    {
        "provider": "google",
        "model": "gemini-3.1-pro-preview",
        "input_price_per_m": 2.00,   # prompts <= 200K tokens
        "output_price_per_m": 12.00,  # includes thinking tokens
        # Gemini's thinking counts against this cap; at 8192 it used ~7.9K
        # tokens thinking and the table was cut off after 6 rows.
        "max_output_tokens": 32768,
    },
]

# Default cap when a model has no "max_output_tokens": room for a 20+ row
# table with reasons. It is a ceiling -- only tokens actually used are billed.
MAX_OUTPUT_TOKENS = 8192

# Appended to every persona so the answer can always be parsed.
FORMAT_TEMPLATE = """

Companies:
{companies}

Answer with a single markdown table with exactly these columns:
| Rank | Company | Reason |

Rules:
- Rank 1 is the best choice, rank {n} is the worst.
- Include every company exactly once, and no other companies.
- Write each company name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the company this rank.
"""


def build_prompt(persona: str, companies: list[str]) -> str:
    return persona.strip() + FORMAT_TEMPLATE.format(
        companies="\n".join(f"- {c}" for c in companies), n=len(companies)
    )


# ---------------------------------------------------------------------------
# Provider calls -- each returns (text, input_tokens, output_tokens, reasoning_tokens)
# ---------------------------------------------------------------------------

@dataclass
class Result:
    provider: str
    model: str
    run: int
    response: str
    input_tokens: int
    output_tokens: int          # everything billed as output (incl. reasoning)
    reasoning_tokens: int       # subset of output_tokens, where reported
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    latency_s: float
    error: str = ""


def call_anthropic(model: str, prompt: str, max_tokens: int):
    import anthropic
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if not workspace_id:
        raise RuntimeError("ANTHROPIC_WORKSPACE_ID environment variable is not set")
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        default_headers={"anthropic-workspace-id": workspace_id},
    )
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    # Anthropic's output_tokens already includes any thinking tokens.
    return text, resp.usage.input_tokens, resp.usage.output_tokens, 0


def call_openai(model: str, prompt: str, max_tokens: int):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_tokens,
    )
    usage = resp.usage
    details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", 0) or 0
    # OpenAI's output_tokens already includes reasoning tokens.
    return resp.output_text, usage.input_tokens, usage.output_tokens, reasoning


def call_google(model: str, prompt: str, max_tokens: int):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    um = resp.usage_metadata
    candidates = um.candidates_token_count or 0
    thoughts = getattr(um, "thoughts_token_count", 0) or 0
    # Gemini reports thinking separately but bills it at the output rate.
    return resp.text or "", um.prompt_token_count or 0, candidates + thoughts, thoughts


CALLERS = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "google": call_google,
}


def run_one(cfg: dict, prompt: str, run: int) -> Result:
    start = time.perf_counter()
    try:
        max_tokens = cfg.get("max_output_tokens", MAX_OUTPUT_TOKENS)
        text, in_tok, out_tok, reasoning = CALLERS[cfg["provider"]](cfg["model"], prompt, max_tokens)
        error = ""
    except Exception as e:  # keep going if one call fails
        text, in_tok, out_tok, reasoning = "", 0, 0, 0
        error = f"{type(e).__name__}: {e}"
    latency = time.perf_counter() - start

    in_cost = in_tok * cfg["input_price_per_m"] / 1_000_000
    out_cost = out_tok * cfg["output_price_per_m"] / 1_000_000
    return Result(
        provider=cfg["provider"],
        model=cfg["model"],
        run=run,
        response=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        reasoning_tokens=reasoning,
        total_tokens=in_tok + out_tok,
        input_cost_usd=round(in_cost, 8),
        output_cost_usd=round(out_cost, 8),
        total_cost_usd=round(in_cost + out_cost, 8),
        latency_s=round(latency, 3),
        error=error,
    )


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

def load_companies(path) -> list[str]:
    """Read the 'company' column of the companies CSV."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row["company"].strip() for row in csv.DictReader(f) if row["company"].strip()]


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[*_`]", "", name.lower())          # markdown emphasis
    name = re.sub(r"\bs\.?\s*p\.?\s*a\.?", " ", name)   # S.p.A. / SPA
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return " ".join(name.split())


def match_company(cell: str, companies: list[str]) -> str | None:
    """Map a company name written by the model back to the canonical name."""
    by_norm = {normalize(c): c for c in companies}
    key = normalize(cell)
    if not key:
        return None
    if key in by_norm:
        return by_norm[key]
    close = difflib.get_close_matches(key, by_norm, n=1, cutoff=0.75)
    if close:
        return by_norm[close[0]]
    # Abbreviated name, e.g. "Poste Vita" -> "Gruppo Assicurativo Poste Vita".
    # Only accept it when exactly one company contains all the words.
    words = set(key.split())
    subset = [c for n, c in by_norm.items() if words <= set(n.split())]
    return subset[0] if len(subset) == 1 else None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_ranking(text: str, companies: list[str]) -> dict:
    """Return {"ranks": {company: rank}, "reasons": {company: text}, "unmatched": [rows]}."""
    ranks: dict[str, int] = {}
    reasons: dict[str, str] = {}
    unmatched: list[str] = []
    row_no = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # separator row
        if cells and normalize(cells[0]) == "rank":
            continue  # header row

        rank_m = re.search(r"\d+", cells[0]) if cells else None
        company, company_col = None, 0
        for i, cell in enumerate(cells):
            if i == 0 and len(cells) > 1:
                continue  # rank column
            company = match_company(cell, companies)
            if company:
                company_col = i
                break
        if company is None:
            unmatched.append(line)
            continue

        row_no += 1
        if company in ranks:
            continue  # model listed a company twice: keep the first placement
        ranks[company] = int(rank_m.group()) if rank_m else row_no
        reasons[company] = " | ".join(c for c in cells[company_col + 1:] if c)
    return {"ranks": ranks, "reasons": reasons, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def rank_stats(calls, companies, model_names) -> dict:
    """{(company, model): (mean, stdev, n_runs)} over the runs that ranked it."""
    stats = {}
    for c in companies:
        for m in model_names:
            ranks = [call["ranks"][c] for call in calls
                     if call["model"] == m and c in call["ranks"]]
            if ranks:
                sd = statistics.pstdev(ranks) if len(ranks) > 1 else 0.0
                stats[c, m] = (statistics.mean(ranks), sd, len(ranks))
    return stats


def build_rank_table(companies, model_names, stats) -> list[dict]:
    rows = []
    for c in companies:
        row = {"company": c}
        means = []
        for m in model_names:
            if (c, m) in stats:
                row[m] = round(stats[c, m][0], 2)
                means.append(stats[c, m][0])
            else:
                row[m] = ""
        row["avg_rank"] = round(float(statistics.mean(means)), 2) if means else ""
        rows.append(row)
    rows.sort(key=lambda r: (r["avg_rank"] == "", r["avg_rank"] or 0))
    return rows


def build_reasons_table(rank_rows, model_names, calls) -> list[dict]:
    """Same shape and row order as the rank table, with each model's reason per cell."""
    rows = []
    for r in rank_rows:
        row = {"company": r["company"]}
        for m in model_names:
            texts = [(call["run"], call["reasons"][r["company"]]) for call in calls
                     if call["model"] == m and r["company"] in call["reasons"]]
            if len(texts) == 1:
                row[m] = texts[0][1]
            else:
                row[m] = "\n".join(f"[run {run}] {t}" for run, t in texts)
        rows.append(row)
    return rows


def build_long_table(calls, companies) -> list[dict]:
    return [
        {"run": call["run"], "provider": call["provider"], "model": call["model"],
         "company": c, "rank": call["ranks"].get(c, ""), "reason": call["reasons"].get(c, "")}
        for call in calls if not call["error"]
        for c in companies
    ]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_table(rows: list[dict], model_names: list[str]) -> None:
    cols = ["company", *model_names, "avg_rank"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    print("\n" + header)
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(
            str(r[c]).ljust(widths[c]) if c == "company" else str(r[c]).rjust(widths[c])
            for c in cols
        ))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    # utf-8-sig so Excel shows accented names (CRÈDIT, Società) correctly
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def md_cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def md_table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(md_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def write_report(path, *, persona_path, prompt, model_names, runs, rank_rows,
                 reason_rows, stats, calls, companies, started) -> None:
    def cell(company, model):
        if (company, model) not in stats:
            return "–"
        mean, sd, _ = stats[company, model]
        return f"{mean:.1f} ± {sd:.1f}" if runs > 1 else f"{mean:g}"

    issues = []
    for call in calls:
        tag = f"`{call['model']}` run {call['run']}"
        if call["error"]:
            issues.append(f"- {tag}: error `{call['error']}`")
            continue
        missing = [c for c in companies if c not in call["ranks"]]
        if missing:
            issues.append(f"- {tag}: did not rank {', '.join(missing)}")
        if call["unmatched"]:
            issues.append(f"- {tag}: {len(call['unmatched'])} table row(s) could not be matched "
                          "to a company (see `raw.json`)")

    total_cost = sum(c["total_cost_usd"] for c in calls)
    rank_note = ("Mean rank over runs ± standard deviation." if runs > 1 else "Rank given by each model.")
    parts = [
        "# What AI models say about Italian life insurers",
        "",
        f"- **Run (UTC):** {started:%Y-%m-%d %H:%M}",
        f"- **Persona:** `{persona_path}`",
        f"- **Models:** {', '.join(f'`{m}`' for m in model_names)}",
        f"- **Runs per model:** {runs}",
        f"- **Total API cost:** ${total_cost:.4f}",
        "",
        "## Ranking (1 = best)",
        "",
        f"{rank_note} Sorted by the average across models.",
        "",
        md_table(["#", "Company", *model_names, "Average"],
                 [[i, r["company"], *(cell(r["company"], m) for m in model_names), r["avg_rank"]]
                  for i, r in enumerate(rank_rows, 1)]),
        "",
        "## Why each model ranked each company",
        "",
        md_table(["Company", *model_names],
                 [[r["company"], *(r[m] for m in model_names)] for r in reason_rows]),
        "",
        "## Issues",
        "",
        "\n".join(issues) if issues else "None.",
        "",
        "## Prompt",
        "",
        "```text",
        prompt.strip(),
        "```",
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


def save(out_dir: Path, *, calls, companies, model_names, runs, persona_path, prompt,
         started) -> list[dict]:
    """Write all output files and return the rank table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = rank_stats(calls, companies, model_names)
    rank_rows = build_rank_table(companies, model_names, stats)
    reason_rows = build_reasons_table(rank_rows, model_names, calls)

    write_csv(out_dir / "rankings.csv", rank_rows, ["company", *model_names, "avg_rank"])
    write_csv(out_dir / "reasons.csv", reason_rows, ["company", *model_names])
    write_csv(out_dir / "long.csv", build_long_table(calls, companies),
              ["run", "provider", "model", "company", "rank", "reason"])
    write_report(out_dir / "report.md", persona_path=persona_path, prompt=prompt,
                 model_names=model_names, runs=runs, rank_rows=rank_rows,
                 reason_rows=reason_rows, stats=stats, calls=calls,
                 companies=companies, started=started)
    with open(out_dir / "raw.json", "w", encoding="utf-8") as f:
        json.dump({"persona": persona_path, "prompt": prompt, "runs": runs, "calls": calls},
                  f, ensure_ascii=False, indent=2)
    return rank_rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_personas(paths=None) -> list[Path]:
    """The given persona files, or every .txt file in personas/ (sorted by name)."""
    if paths:
        return [Path(p) for p in paths]
    personas = sorted(PERSONAS_DIR.glob("*.txt"))
    if not personas:
        raise SystemExit(f"No persona files (*.txt) found in {PERSONAS_DIR}")
    return personas


def persona_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # company names like CRÈDIT on Windows consoles
    parser = argparse.ArgumentParser(description="Rank Italian life insurers with several LLMs.")
    parser.add_argument(
        "--provider",
        choices=["all", *CALLERS],
        default="all",
        help="Run only one provider, or all providers (default: all).",
    )
    parser.add_argument("--companies", default=COMPANIES_PATH,
                        help="CSV with a 'company' column (default: companies.csv).")
    parser.add_argument("--persona", action="append",
                        help="Persona text file; repeat to run several. "
                             "Default: every .txt file in personas/.")
    parser.add_argument("--runs", type=int, default=1,
                        help="Times to ask each model; ranks are averaged (default: 1).")
    parser.add_argument("--output-dir", default=OUTPUT_DIR,
                        help="Folder for results; each run gets a timestamped subfolder.")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    companies = load_companies(args.companies)
    personas = load_personas(args.persona)
    missing_files = [str(p) for p in personas if not p.is_file()]
    if missing_files:  # check up front so no API calls are paid for before failing
        parser.error(f"persona file(s) not found: {', '.join(missing_files)}")
    stems = [p.stem for p in personas]
    if len(set(stems)) != len(stems):
        parser.error("persona files must have different names (they become output folder names)")
    models = MODELS if args.provider == "all" else [
        cfg for cfg in MODELS if cfg["provider"] == args.provider
    ]
    model_names = [cfg["model"] for cfg in models]
    total = len(personas) * len(models) * args.runs
    print(f"{len(companies)} companies, {len(personas)} persona(s), {len(models)} model(s), "
          f"{args.runs} run(s) each = {total} calls")

    started = datetime.now(timezone.utc)
    run_dir = Path(args.output_dir) / started.strftime("%Y%m%dT%H%M%SZ")
    total_cost, n = 0.0, 0
    for persona_path in personas:
        label = persona_label(persona_path)
        print(f"\n=== Persona: {label} ===")
        prompt = build_prompt(persona_path.read_text(encoding="utf-8"), companies)
        calls = []
        for cfg in models:
            for run in range(1, args.runs + 1):
                n += 1
                print(f"[{n}/{total}] {cfg['model']} run {run}", end=" ... ", flush=True)
                r = run_one(cfg, prompt, run)
                if r.error:
                    print(f"ERROR {r.error}")
                    parsed = {"ranks": {}, "reasons": {}, "unmatched": []}
                else:
                    parsed = parse_ranking(r.response, companies)
                    print(f"ranked {len(parsed['ranks'])}/{len(companies)}, "
                          f"{r.total_tokens} tok, ${r.total_cost_usd:.6f}")
                    missing = [c for c in companies if c not in parsed["ranks"]]
                    if missing:
                        print(f"    missing: {', '.join(missing)}")
                    if parsed["unmatched"]:
                        print(f"    unmatched rows: {len(parsed['unmatched'])} (see raw.json)")
                calls.append({**asdict(r), **parsed})

        # Save each persona as soon as it is done, so a later failure loses nothing.
        out_dir = run_dir / persona_path.stem
        rank_rows = save(out_dir, calls=calls, companies=companies, model_names=model_names,
                         runs=args.runs, persona_path=label, prompt=prompt, started=started)
        print_table(rank_rows, model_names)
        print(f"\nSaved rankings.csv, reasons.csv, long.csv, report.md and raw.json to {out_dir}")
        total_cost += sum(c["total_cost_usd"] for c in calls)

    print(f"\nTotal cost: ${total_cost:.6f}")


if __name__ == "__main__":
    main()
