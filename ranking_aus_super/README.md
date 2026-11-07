# What do AI assistants tell Australians about super fees?

**Ask leading AI models to rank Australian superannuation funds from cheapest to most expensive for a given member, and compare their answers with the fees the funds actually charge.**

## Why this matters

More and more people ask an AI assistant such as ChatGPT, Claude or Gemini questions like:

> *"I'm 36 with $150,000 in super. Which fund would charge me the least?"*

The answer shapes which funds they look at. This repository measures how good those answers are:

- Do the models put the funds in the right order of cost?
- How far are their dollar estimates from the fees in the funds' own disclosures?
- Which part do they get most wrong: administration fees, investment fees or transaction costs?
- Do different models agree with each other? (With `--runs`, `long.csv` also shows how much one model's answers vary between runs.)

## How it works

```text
inputs/companies.csv ─┐
                      ├─► prompt ─► each model (× runs) ─► parse table ──┐
personas/*.txt ───────┘                                                    ├─► compare ─► results/<timestamp>/<persona>/
inputs/fees.csv ───────────► actual fees for the persona's balance ───────┘
```

For **each persona** in [`personas/`](personas/), **each model** and **each run**:

1. **Build the prompt** from the persona text, the funds in [`inputs/companies.csv`](inputs/companies.csv) and fixed answer-format rules (see [The prompt](#the-prompt)).
2. **Ask the model.** Each call is a single, independent request: no conversation history, no system prompt, no web search, and the provider's default settings apart from the output-token cap.
3. **Parse the answer.** The model's table gives, for each fund, a rank, estimated administration fees, investment fees, transaction costs and total in dollars, and a reason.
4. **Calculate the actual fees** for the persona's balance from [`inputs/fees.csv`](inputs/fees.csv).
5. **Compare and save.** Ranks and dollar amounts are compared fund by fund, and the results are written to `results/<timestamp>/<persona>/`.

If a call fails (a missing API key, a provider outage), the error is printed and recorded, and the loop carries on.

Models used by default (the `MODELS` list at the top of [`llm_super_ranking.py`](llm_super_ranking.py)):

| Provider | Model | Output-token cap |
|---|---|---|
| Anthropic | `claude-sonnet-5` | 16,384 |
| OpenAI | `gpt-5.6-terra` | 32,768 |
| Google | `gemini-3.1-pro-preview` | 32,768 |

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
| `ANTHROPIC_WORKSPACE_ID` | Claude; sent as the `anthropic-workspace-id` header |
| `OPENAI_API_KEY` | GPT |
| `GEMINI_API_KEY` | Gemini |

```bash
export ANTHROPIC_API_KEY=...       # PowerShell: $env:ANTHROPIC_API_KEY = "..."
export ANTHROPIC_WORKSPACE_ID=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
```

A missing variable only affects that provider: its calls are recorded as errors and the other models still run.

Run it:

```bash
python llm_super_ranking.py                       # every persona, every model, one run each
python llm_super_ranking.py --runs 5              # ask each model 5 times; ranks and fees are averaged
python llm_super_ranking.py --provider anthropic  # only one provider
```

| Option | Default | What it does |
|---|---|---|
| `--runs N` | `1` | Ask each model N times per persona |
| `--provider {all,anthropic,openai,google}` | `all` | Run all models, or only those from one provider |

### Cost

In the October 2026 run, 30 funds and one persona with `--runs 5` cost **US$1.27** for 15 calls, or about US$0.25 per run of all three models: roughly $0.05 for GPT, $0.08 for Claude and $0.12 for Gemini, which spends the most tokens thinking. The cost of a call varied by up to about 20% between runs. The total scales with personas × runs. The cost of each call is printed as it runs, the total is printed at the end, and both are saved in `raw.json`. Costs are calculated from the prices in `MODELS`; check them against the providers' pricing pages before relying on them.

## Inputs

### `inputs/companies.csv`

The funds to rank: a `company` header, then one name per line. These are the names the models see, so they should be names a member would recognise. The list was built from APRA's list of RSE licensees and MySuper authorised products (see [`source/`](source/)). Duplicate names are dropped.

### `inputs/fees.csv`

The actual fees of one investment option per fund. The prompt tells the models which option that is, using `OPTION` at the top of the script: currently each fund's growth or high-growth option, or for a lifecycle product the stage for the persona's age. If you change which options `fees.csv` holds, change `OPTION` to match. One row per fund:

| Column | Meaning |
|---|---|
| `company` | Fund name, exactly as in `companies.csv` |
| `admin_fee_fixed_aud` | Fixed administration fee, dollars per year |
| `admin_fee_rate` | Administration fee as a share of the balance per year |
| `investment_fee_rate` | Investment fees and costs as a share of the balance per year |
| `transaction_cost_rate` | Transaction costs as a share of the balance per year |
| `source` | Where the figures come from: a web page, or a file in `source/fund_reports_on_fees/` |

Rates are **decimals**, the way Excel stores percentages: 0.50% p.a. is `0.005`. If you save the file from Excel, format the rate cells as *General* first; cells formatted as percentages are saved as text such as `0.50%`, which the script cannot read.

For a persona with balance *B*:

```text
admin  = admin_fee_fixed_aud + B × admin_fee_rate
invest = B × investment_fee_rate
trans  = B × transaction_cost_rate
total  = admin + invest + trans        (funds are ranked by total)
```

Every fund in `companies.csv` must appear in `fees.csv` with the same name; otherwise the script stops before calling any model. The fees were collected by hand from each fund's PDS or fees-and-costs page and checked in `source/Manual calc.xlsx`. Funds change their fees, so check the sources before relying on the figures. Lifecycle products can charge different investment fees at different ages, while `fees.csv` holds one set of rates per product, so make sure they match the persona's age.

### `personas/*.txt`

One file per member, describing the member and their balance, and asking for the ranking. For example, [`personas/mid_career.txt`](personas/mid_career.txt):

```text
You are a 36-year-old Australian with $150,000 in super. You are quite risk tolerant and are not afraid of riskier options if it means more potential growth.

Rank the following Australian superannuation funds from lowest to highest total annual fees for you.
```

The balance used for the actual fees is the **first dollar amount** in the text ($50,000 if there is none). Every `.txt` file in the folder is run, and its name becomes the name of its output folder.

## The prompt

The script adds the fund list and fixed format rules to the persona text, so every answer can be parsed:

```text
Funds:
- QSuper Lifetime
- AustralianSuper
- …

Answer with a single markdown table with exactly these columns:
| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |

Rules:
- Rank 1 has the lowest total annual fees and costs for you, rank 30 the highest.
- The dollar columns are your estimates for one year in the fund's growth or high-growth investment option (for a lifecycle product, the stage for your age) on your balance, each as a single dollar amount: administration fees, investment fees, transaction costs, and their total.
- Include every fund exactly once, and no other funds.
- Write each fund name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the fund this rank.
```

The full prompt of every run is saved in `raw.json`.

## Output

Each invocation writes to `results/<timestamp>/`, with one subfolder per persona:

| File | Contents |
|---|---|
| `summary.csv` | One row per fund, cheapest first. Columns are grouped by measure, actual value first and then each model: rank, total, admin fees, investment fees, transaction costs. With `--runs` above 1, model values are means over the runs |
| `accuracy.csv` | One row per model: `rank_corr`, the median absolute % error of the total and of each part, `funds_ranked_per_run`, `total_mismatches_per_run` |
| `agreement.csv` | How closely the rankings agree. The actual ranking and each model's ranking appear as both rows and columns; each cell is the rank correlation between the two (1 = same order). With `--runs` above 1, each model's mean rank is used |
| `long.csv` | One row per run × model × fund: actual and estimated value with the % error, for the rank, total and each part; `total_gap`; the model's reason |
| `raw.json` | The prompt, the balance, and for every call the full response, token counts, cost and any error |

The CSV files are UTF-8 with a byte-order mark, so they open correctly in Excel.

### How the numbers are calculated

- **`rank_corr`**: correlation between the model's ranks and the actual ranks, over all its runs. 1 means the same order, 0 means no relation. With no tied ranks it equals Spearman's rank correlation.
- **`agreement.csv`**: Spearman's rank correlation between every pair of rankings, over the funds both ranked. The `actual` row shows how well each model's order matches the fees; the other cells show how far the models agree with each other. With one run and no tied ranks, the `actual` row equals `rank_corr` in `accuracy.csv`.
- **`abs_pct_err_*`**: |estimate − actual| / actual × 100, for the total and each part. `accuracy.csv` shows the median per model.
- **`total_gap`**: the model's total minus the sum of its own three parts, which checks the model's arithmetic. `total_mismatches_per_run` counts funds where the gap is more than $5 (`TOTAL_TOLERANCE` in the script).
- **`funds_ranked_per_run`**: how many funds the model ranked, on average. Below the number of funds means it left some out or renamed them beyond recognition.

### How answers are parsed

- A table row is any line with at least seven cells and a number in the first one.
- Fund names are matched loosely: case, spaces, punctuation and markdown such as `**bold**` are ignored, and a name at least 80% similar to one in the list is accepted. Rows that match no fund are skipped; if a fund appears twice, the first row is kept.
- Dollar cells are read as their first number, so `$1,234` is 1234 and a range such as `$300–400` counts as 300. A cell with no number, such as `unknown`, is left blank and excluded from the error figures.

## How to read the results

- **This measures what the models say, not what the funds charge.** The models answer from their training data, without web search, and their knowledge can be out of date. That is the point: the tool shows what a member asking an AI assistant would be told.
- **Consumer apps may answer differently.** The script calls the models through their APIs, without the web search and extra instructions that apps such as ChatGPT or Gemini can add.
- **Answers vary.** The same model can rank a fund quite differently on two runs. Use `--runs 5` or more before drawing conclusions.
- **Ranking forces an order.** Many funds' totals are within a few dollars of each other, so small rank differences mean little; the dollar errors are often more informative.
- **Names matter.** A product name a model does not recognise (a product name rather than the fund's brand, say) gets a guess.
- Nothing produced by this tool is financial advice or an assessment of any fund.

## Project structure

```text
llm_super_ranking.py          the script: models, prompt, API calls, parsing, comparison, outputs
inputs/
  companies.csv               funds to rank (the names the models see)
  fees.csv                    each fund's actual fees and their source
personas/                     member scenarios, one .txt file each (all are run)
source/
  List of RSES and RSE Licensees and MySuper Authorised products 19 October 2026.xlsx
                              APRA's list the fund list was built from
  Product_list_assembly.xlsx  how the fund list was assembled from it
  Manual calc.xlsx            hand calculation of the fees, used to check the script
  fund_reports_on_fees/       PDSs and fee guides behind fees.csv
results/                      generated output, one timestamped folder per run
requirements.txt              anthropic, openai, google-genai, pandas
LICENSE                       MIT
```

## Limitations of this version

This is a deliberately small first version. Parsing and input checks are minimal, there is no web-search option and no automated tests.

## License

[MIT](LICENSE)

## Results of the October 2026 run

Five runs of each model (`--runs 5`), one persona ([`personas/mid_career.txt`](personas/mid_career.txt): a 36-year-old with A$150,000 in super who is comfortable with risk) and the 30 products listed below. The full output is in `results/20261026T020224Z/`.

| Model | Rank correlation with the actual ranking | Range over the 5 runs | Median error on the annual total | Estimates above the actual total |
|---|---|---|---|---|
| `claude-sonnet-5` | 0.17 | 0.10–0.21 | 27% | 74% |
| `gpt-5.6-terra` | 0.36 | 0.23–0.46 | 19% | 77% |
| `gemini-3.1-pro-preview` | 0.28 | 0.24–0.36 | 30% | 91% |

- **The models agree with each other far more than with the fees.** Their average rankings correlate with each other at 0.73–0.83, but with the actual ranking at only 0.15–0.42 (`agreement.csv`).
- **The cheapest product costs A$360 a year.** On average the models ranked it between 19th and 28th of 30 and estimated its fees at A$1,475–2,088. Single runs placed it anywhere from 6th to 30th.
- In two of its five runs, Claude wrote "HOSTPLUS" instead of the listed name, which the parser did not match, so those two answers cover 29 funds.

## Products used in the October 2026 run

The October 2026 run (five runs of each model) used one persona, [`personas/mid_career.txt`](personas/mid_career.txt): a 36-year-old Australian with $150,000 in super who is comfortable with investment risk. For each of the 30 products in `inputs/companies.csv`, `inputs/fees.csv` holds the fees of the option below; the prompt asks the models about each fund's growth or high-growth option, or the stage for the persona's age (see [The prompt](#the-prompt)). The fees were taken by hand from the fund's PDS, fee guide or fees page in October 2026 and checked in `source/Manual calc.xlsx`.

The table is sorted from cheapest to most expensive for this persona. **Fund** is the name the models were given; **Admin** is the percentage-based administration fee the member pays.

| # | Fund | Option used for the fees | Fixed admin (per year) | Admin | Investment | Transaction | Total on $150,000 | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | Essential Super | High Growth Index | $60 | 0.04% | 0.16% | 0.00% | $360 | [Essential Super_fs7915.pdf](source/fund_reports_on_fees/Essential%20Super_fs7915.pdf) |
| 2 | Vanguard MySuper | Lifecycle, age 47 and under | $0 | 0.33% | 0.21% | 0.02% | $840 | [vanguard.com.au](https://www.vanguard.com.au/super/performance-and-fees/fees/superannuation) |
| 3 | UniSuper | High Growth | $96 | 0.00% | 0.41% | 0.09% | $846 | [unisuper.com.au](https://www.unisuper.com.au/super/fees-and-costs) |
| 4 | AustralianSuper | PreMixed High Growth | $52 | 0.10% | 0.45% | 0.06% | $967 | [fees-and-costs_AustralianSuper.pdf](source/fund_reports_on_fees/fees-and-costs_AustralianSuper.pdf) |
| 5 | Team Super | High Growth | $39 | 0.16% | 0.39% | 0.07% | $969 | [teamsuper.com](https://www.teamsuper.com/Members/super/super-explained/fees-and-taxes) |
| 6 | QSuper Lifetime | High Growth | $57.20 | 0.06% | 0.50% | 0.05% | $972 | [qsuper.qld.gov.au](https://qsuper.qld.gov.au/our-products/our-fees) |
| 7 | AMP MySuper Options | 1990s Plus | $78 | 0.204% | 0.37% | 0.04% | $999 | [amp.com.au](https://www.amp.com.au/superannuation/amp-super/fees-and-costs) |
| 8 | NESS Super | High Growth | $72.80 | 0.1275% | 0.49% | 0.02% | $1,029 | [nesssuper.com.au](https://nesssuper.com.au/pds/) |
| 9 | smartMonday LifeStage | LifeStage – Age 50 and under | $67.60 | 0.16% | 0.454% | 0.03% | $1,034 | [Smart_Monday_1786595961-final_sm_pds_tesf_14082026.pdf](source/fund_reports_on_fees/Smart_Monday_1786595961-final_sm_pds_tesf_14082026.pdf); investment and transaction costs from the *How smartMonday PRIME TESF Works* guide (14 August 2026) |
| 10 | Rest Super | High Growth | $78 | 0.10% | 0.47% | 0.07% | $1,038 | [rest.com.au](https://rest.com.au/super/products/fees-and-costs) |
| 11 | OneSuper ING | ING Living Super – High Growth | $60 | 0.35% | 0.31% | 0.00% | $1,050 | [ING_DIRECT_Living_Super_Product_Guide.pdf](source/fund_reports_on_fees/ING_DIRECT_Living_Super_Product_Guide.pdf) |
| 12 | MySuper Lifecycle | High Growth (Aware Super) | $52 | 0.15% | 0.44% | 0.08% | $1,057 | [aware.com.au](https://aware.com.au/member/what-we-offer/fees-and-costs) |
| 13 | HESTA Super Fund | High Growth | $52 | 0.15% | 0.51% | 0.04% | $1,102 | [hesta.com.au](https://www.hesta.com.au/members/your-superannuation/fees-and-costs) |
| 14 | FIRST Super | Growth | $85.80 | 0.18% | 0.44% | 0.06% | $1,106 | [firstsuper.com.au](https://www.firstsuper.com.au/superannuation/fees-costs/) |
| 15 | Brighter Super | Growth | $26 | 0.14% | 0.52% | 0.06% | $1,106 | [brightersuper.com.au](https://www.brightersuper.com.au/compare/fees-and-costs) |
| 16 | Cbus Super | High Growth | $52 | 0.19% | 0.45% | 0.08% | $1,132 | [cbussuper.com.au](https://www.cbussuper.com.au/fees) |
| 17 | CareSuper | Growth | $67.60 | 0.15% | 0.48% | 0.08% | $1,133 | [caresuper.com.au](https://www.caresuper.com.au/members/why-choose-us/fees-and-costs) |
| 18 | Vision Active Super | High growth (Accelerator) | $66.04 | 0.14% | 0.54% | 0.06% | $1,176 | [visionsuper.com.au](https://www.visionsuper.com.au/forms-and-resources/pds) |
| 19 | Prime Super | Managed Growth | $52 | 0.25% | 0.39% | 0.13% | $1,207 | [primesuper.com.au](https://primesuper.com.au/member/super/fees) |
| 20 | REI Super | Growth Plus | $0 | 0.25% | 0.59% | 0.00% | $1,260 | [rei-super-fees-and-costs.pdf](source/fund_reports_on_fees/rei-super-fees-and-costs.pdf) |
| 21 | HOSTPLUS Superannuation Fund | High Growth | $78 | 0.00% | 0.68% | 0.11% | $1,263 | [HOSTPLUS_fees-and-costs-guide.pdf.coredownload.pdf](source/fund_reports_on_fees/HOSTPLUS_fees-and-costs-guide.pdf.coredownload.pdf) |
| 22 | FirstChoice Employer Super | CFS High Growth | $60 | 0.04% | 0.73% | 0.04% | $1,275 | [FirstChoice Employer Super_fs8688.pdf](source/fund_reports_on_fees/FirstChoice%20Employer%20Super_fs8688.pdf) |
| 23 | Russell Investments Master Trust | GoalTracker (iQ Super – For Life), age 15–50 | $60 | 0.17% | 0.62% | 0.04% | $1,305 | [russellinvestments.com](https://russellinvestments.com/content/ri/au/en-gb/member/superannuation/why-choose-us/iq-super-for-life.html) |
| 24 | BUSSQ Superannuation | Balanced Growth | $0 | 0.18% | 0.60% | 0.12% | $1,350 | [bussq.com.au](https://www.bussq.com.au/superannuation/super-with-bussq/fees-and-costs) |
| 25 | Equipsuper | Growth Plus | $52 | 0.19% | 0.56% | 0.14% | $1,387 | [equipsuper.com.au](https://www.equipsuper.com.au/your-super/fees-and-costs) |
| 26 | legalsuper | High Growth | $51.48 | 0.29% | 0.52% | 0.09% | $1,401 | [legalsuper.com.au](https://www.legalsuper.com.au/why-join-legalsuper/superannuation-fees?openAccordions=What-do-administration-fees-cover%3F) |
| 27 | Mercer SmartPath | SmartPath, born after 1973¹ | $0 | 0.18% | 0.70% | 0.06% | $1,410 | [mercersuper.com.au](https://www.mercersuper.com.au/documents/product-disclosure-statement/) |
| 28 | IOOF MYSUPER | IOOF MySuper (Balanced Growth) | $78 | 0.25% | 0.61% | 0.08% | $1,488 | [ioof.com.au](https://www.ioof.com.au/superannuation/mysuper-dashboard) |
| 29 | MLC Australia | MLC High Growth | $60 | 0.17% | 0.83% | 0.03% | $1,605 | [mlc.com.au](https://www.mlc.com.au/adviser/super/products/mlc-masterkey-super-fundamentals/fees) |
| 30 | NGS Super | Growth (formerly High Growth) | $65 | 0.17% | 0.87% | 0.04% | $1,685 | [ngssuper.com.au](https://www.ngssuper.com.au/files/documents/fees-costs-and-tax.pdf) |

¹ Mercer's PDS gives investment fees of 0.52%–0.70% and transaction costs of 0.01%–0.06% depending on the member's SmartPath, without per-path figures; the top of each range is used.

- **Excluded:** administration costs that funds pay from their reserves rather than charging to members' accounts.
- **Investment fees** include performance fees. **Transaction costs** are net of amounts recovered through buy-sell spreads.
- **Fees change.** AustralianSuper's percentage administration fee rises from 0.10% to 0.12% on 31 October 2026, and AMP's fees change on 1 December 2026. Re-check the sources before reusing these figures.
