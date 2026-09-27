# What do AI assistants tell your customers?

**Ask leading AI models to rank Italian life insurers from a customer's point of view, and see what each model says about each company.**

## Why this matters

Customers used to compare life insurance through agents, bank advisers, comparison sites and word of mouth. More and more of them now start by asking an AI assistant such as ChatGPT, Claude or Gemini:

> *"We're a young family in Italy looking for our first life insurance. Which company should we go with?"*

The answer they get shapes their shortlist before they ever visit your website or speak to an adviser. Yet most insurers have no idea what these models say about them:

- Where does your company appear in the ranking: near the top, in the middle, or last?
- *Why* does the model place you there? Brand strength, financial solidity, product complexity, costs, distribution?
- Do different models agree with each other, and does the same model give the same answer twice?
- Is the model's picture of your company accurate, or out of date?

This repository makes those questions measurable. It puts the same question, framed as a specific customer, to several AI models and turns their answers into comparable tables of **ranks** and **reasons**, one row per company.

## How it works

```text
companies.csv ─┐
               ├─► prompt ─► each model (× runs) ─► parse table ─► results/<timestamp>/<persona>/
personas/*.txt ┘
```

For **each persona** in [`personas/`](personas/), and for **each model**, and for **each run**:

1. **Build the prompt.** The persona text, the list of companies from [`companies.csv`](companies.csv) and fixed answer-format rules (see [The prompt](#the-prompt)).
2. **Ask the model.** Each call is a single, independent request: no conversation history, no system prompt, no web search and the provider's default settings.
3. **Parse the answer.** The model's `| Rank | Company | Reason |` table is read row by row, and each company name is matched back to the name in `companies.csv` (see [How answers are parsed](#how-answers-are-parsed)).

When all models have answered for a persona, its results are saved to `results/<timestamp>/<persona>/` and its ranking table is printed in the terminal. Then the next persona starts. Every persona is saved as soon as it finishes, so a failure later in the loop does not lose earlier results.

If a call fails (missing API key, missing package, provider outage), the error is printed and recorded, and the loop carries on with the next call.

Models used by default:

| Provider | Model | Output-token cap |
|---|---|---|
| Anthropic | `claude-sonnet-5` | 8,192 |
| OpenAI | `gpt-5.6-terra` | 8,192 |
| Google | `gemini-3.1-pro-preview` | 32,768 |

Gemini gets a higher cap because its internal "thinking" counts against the limit. At 8,192 it once spent almost the whole budget thinking and its table was cut off after 6 rows. The cap is a ceiling; you only pay for tokens actually used.

## Quick start

Requires Python 3.10+ and an API key for each provider you want to use.

```bash
git clone <this-repo-url>
cd <repo-folder>
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set the environment variables for the providers you will use:

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude |
| `ANTHROPIC_WORKSPACE_ID` | Claude: required; sent as the `anthropic-workspace-id` header |
| `OPENAI_API_KEY` | GPT |
| `GEMINI_API_KEY` | Gemini |

```bash
export ANTHROPIC_API_KEY=...
export ANTHROPIC_WORKSPACE_ID=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
```

PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "..."
$env:ANTHROPIC_WORKSPACE_ID = "..."
$env:OPENAI_API_KEY = "..."
$env:GEMINI_API_KEY = "..."
```

A missing variable only affects that provider: its calls are recorded as errors and the other models still run. Use `--provider` to skip providers you have no key for.

Run it:

```bash
python llm_life_insurer_ranking.py                  # every persona, every model, one run each
python llm_life_insurer_ranking.py --runs 5         # ask each model 5 times and average
python llm_life_insurer_ranking.py --provider google
python llm_life_insurer_ranking.py --persona personas/young_family.txt   # just one persona
```

### Command-line options

| Option | Default | What it does |
|---|---|---|
| `--provider {all,anthropic,openai,google}` | `all` | Run all models, or only those from one provider |
| `--persona FILE` | every `.txt` in `personas/` | Run only this persona file. Repeat to pick several: `--persona a.txt --persona b.txt` |
| `--runs N` | `1` | Ask each model N times per persona; ranks are averaged over the runs |
| `--companies FILE` | `companies.csv` | CSV file with the companies to rank |
| `--output-dir DIR` | `results` | Where results go; each invocation creates a new `<timestamp>` subfolder here |

Before any API call is made, the script checks that every persona file exists and that no two persona files share a name, since the name becomes the output folder. That way a typo never costs money.

### Cost

Asking all three models about 22 companies costs roughly **$0.10–0.15 per persona per run** at September 2026 list prices. The total scales with personas × runs: the two included personas with `--runs 5` is about $1–1.50. In our runs Gemini was the most expensive, because it spends thousands of tokens thinking before it answers.

The cost of every call is printed as it runs, the total is printed at the end, and the cost per call is saved in `raw.json`. Costs are calculated from the prices in `MODELS` in the script; check them against the providers' pricing pages before relying on them.

## Output

Each invocation writes to `results/<timestamp>/`, with one subfolder per persona. The subfolder is named after the persona file without `.txt`:

```text
results/
└── 20260927T112558Z/
    ├── pre_retirement_saver/
    │   ├── report.md
    │   ├── rankings.csv
    │   ├── reasons.csv
    │   ├── long.csv
    │   └── raw.json
    └── young_family/
        └── ...
```

| File | Contents |
|---|---|
| `report.md` | Readable report: run details and total cost, the ranking table, every model's reason for every company, any issues, and the exact prompt |
| `rankings.csv` | One row per company, one column per model with its rank, plus `avg_rank`. Sorted best first by `avg_rank` |
| `reasons.csv` | Same rows and order as `rankings.csv`; each cell holds the model's explanation for that rank |
| `long.csv` | One row per run × model × company, with columns `run, provider, model, company, rank, reason`: convenient for pivot tables or pandas |
| `raw.json` | The prompt, and for every call the full response, parsed ranks and reasons, unmatched rows, token counts (including reasoning tokens), cost, latency and any error |

The CSV files are UTF-8 with a byte-order mark, so Excel shows accented names such as *CRÈDIT* and *Società* correctly.

### How the numbers are calculated

- **Model columns** in `rankings.csv` are the model's rank for that company. With `--runs N` they are the **mean rank over the runs**. Runs where the model left the company out are not counted.
- **`avg_rank`** is the mean of the model columns, counting only models that ranked the company. Companies no model ranked are listed last with blank cells.
- **`report.md`** shows each rank as `mean ± standard deviation` when `--runs` is above 1. The standard deviation is the population standard deviation over the runs. A dash (–) means the model did not rank that company.
- **`reasons.csv`**: with one run each cell is the model's reason. With several runs the cell lists every run's reason on its own line, as `[run 1] …`, `[run 2] …`.
- **Issues** in `report.md` lists failed calls, companies a model left out, and table rows that could not be matched to a company. The same warnings are printed in the terminal.

## The prompt

A persona file contains only the customer scenario and the request to rank, for example [`personas/young_family.txt`](personas/young_family.txt):

```text
Imagine you are a young Italian family looking to buy your first life insurance product.

Rank the following life insurance companies operating in Italy from best to worst for you.
```

The script then adds the company list and the format rules, so every persona produces answers that can be parsed:

```text
Companies:
- Credemvita S.p.A.
- AXA MPS Assicurazioni Vita
- …

Answer with a single markdown table with exactly these columns:
| Rank | Company | Reason |

Rules:
- Rank 1 is the best choice, rank 22 is the worst.
- Include every company exactly once, and no other companies.
- Write each company name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the company this rank.
```

The full prompt used is saved at the end of every `report.md` and in `raw.json`.

## How answers are parsed

Models don't always copy names exactly, so each row's company name is matched to `companies.csv` as follows:

1. **Normalised exact match.** Case, accents, markdown formatting (`**bold**`) and the "S.p.A." suffix are ignored, so `**Crédit Agricole Vita**` matches `CRÈDIT AGRICOLE VITA`.
2. **Close spelling.** Small typos are accepted (at least 75% similarity).
3. **Abbreviation.** A shortened name is accepted when exactly one company contains all its words, so `Poste Vita` matches `Gruppo Assicurativo Poste Vita`. An ambiguous name such as `UniCredit`, which fits two companies, is **not** matched.

The rank is the number in the table's first column; if a row has no number, its position in the table is used. If a company appears twice, the first placement is kept. Rows that can't be matched, including a row cut off mid-answer, are listed under Issues and saved in `raw.json`, so you can check them.

## Adapting it

| To change… | Edit |
|---|---|
| The companies | [`companies.csv`](companies.csv): a `company` header, then one company per line. From Excel, save as *CSV UTF-8* so accented names survive. |
| The customer | Add a `.txt` file to [`personas/`](personas/); it is picked up automatically on the next run. Write only the scenario and the request to rank; see [The prompt](#the-prompt). |
| The models | `MODELS` at the top of [`llm_life_insurer_ranking.py`](llm_life_insurer_ranking.py): `provider` (`anthropic`, `openai` or `google`), `model` id, `input_price_per_m` and `output_price_per_m` in USD per million tokens, and optionally `max_output_tokens`. The default cap is `MAX_OUTPUT_TOKENS` (8,192). |
| The answer format | `FORMAT_TEMPLATE` in the script. If you change the table columns, update `parse_ranking` to match. |

Ideas for personas: a single parent, a self-employed worker, a couple taking out a mortgage, a 55-year-old saving for retirement ([included](personas/pre_retirement_saver.txt)), a customer who only wants to buy online.

The same approach works for any market or product line: swap the company list and the personas.

## How to read the results

- **This measures perception, not quality.** The models answer from their training data. They do not look at current products, prices, solvency ratios or claims experience, and they can be wrong or out of date. That is the point: the tool shows what a customer asking an AI assistant would be told.
- **Answers vary.** The same model can rank a company quite differently on two runs. Use `--runs 5` or more before drawing conclusions. A large ± spread in `report.md` means the model has no firm view.
- **The reasons are the most useful part.** They show which associations drive a company's position, for example "bank-distributed, complex unit-linked products" or "trusted brand, accessible everywhere". Those associations are what a customer hears.
- **Ranking forces an order.** Even when a model considers two companies equivalent, it must put one above the other. Small rank differences mean little.
- **Consumer assistants may differ.** The script calls the models through their APIs, without web search and without the extra instructions that consumer apps like ChatGPT or Gemini add. Answers in those apps can differ, especially when they search the web.
- Nothing produced by this tool is financial advice or an assessment of any company.

## Tests

The tests cover company-name matching, response parsing, persona discovery, prompt building, aggregation over runs and models, and the output files. They make no API calls and need no keys:

```bash
python -m unittest discover tests
```

## Project structure

```text
llm_life_insurer_ranking.py   the script: models, prompt, API calls, parsing, aggregation, outputs
companies.csv                 companies to rank
personas/                     customer scenarios, one .txt file each (all are run by default)
tests/test_ranking.py         offline tests
requirements.txt              provider SDKs: anthropic, openai, google-genai
results/                      generated output (git-ignored, created on first run)
LICENSE                       MIT
```

## License

[MIT](LICENSE)
