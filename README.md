<div align="center">
<h1 align="center" style="border-bottom: none">
  <b>
    🐍 Actuarial skills 🐍
  </b>
</h1>
</div>

<p align="center">
  Claude skills that speed up actuarial work, and studies of what AI models tell insurance customers.
</p>

<p align="center">
  <a href="https://github.com/open-source-modelling/insurance_skills/actions/workflows/tests.yml"><img src="https://github.com/open-source-modelling/insurance_skills/actions/workflows/tests.yml/badge.svg?branch=main" alt="tests"></a>
</p>

## Claude skills

A skill is a folder of instructions and scripts that Claude reads when a task calls for it. Each skill's README explains how to install it.

| Skill | Source | What it does |
|---|---|---|
| [historic-eiopa-yield-curve] | Built on [EIOPA_all_curves] | Answers questions about historic EIOPA risk-free rate curves: a spot rate, a whole term structure, a tenor over time, countries side by side, forwards, discount factors, the Solvency II stressed curves and curve parameters (UFR, LLP, alpha, CRA, VA). It carries every monthly release since December 2014, so it needs no network, database or extra package. |
| [spontaneous-testing-for-excel] | Original | Runs tests written in plain English in a table on a workbook's `TEST` sheet, and writes the evidence and a verdict for each test back into the workbook. |
| [validate-ul-fund-data] | Original | Runs 22 checks on a quarterly unit-linked fund Excel extract before anyone analyses it, and writes the results to a separate report workbook. |

## LLM studies

Scripts that put the same customer question to several AI models (Claude, GPT and Gemini) through their APIs and turn the answers into tables that can be compared. They call paid APIs, so you need your own API keys. Both are in [`llm-rankings/`](llm-rankings/).

| Study | What it measures |
|---|---|
| [italy-life] | How each model ranks Italian life insurers for a given customer, and the reason it gives for each company. |
| [australia-super] | How well each model ranks Australian super funds by fees for a given member, compared with the fees the funds actually charge. |

## Contributing

Suggestions for new skills and studies are welcome. Queries and suggestions: gregor@osmodelling.com

[historic-eiopa-yield-curve]: historic-eiopa-yield-curve/
[EIOPA_all_curves]: https://github.com/open-source-modelling/EIOPA_all_curves
[spontaneous-testing-for-excel]: spontaneous-testing-for-excel/
[validate-ul-fund-data]: validate-ul-fund-data/
[italy-life]: llm-rankings/italy-life/
[australia-super]: llm-rankings/australia-super/
