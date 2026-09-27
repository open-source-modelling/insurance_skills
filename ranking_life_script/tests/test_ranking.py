"""Offline tests: parsing, name matching and aggregation. No API calls.

Run from the repository root:
    python -m unittest discover tests
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_life_insurer_ranking as m  # noqa: E402

COMPANIES = m.load_companies(m.COMPANIES_PATH)


def table(rows):
    return "| Rank | Company | Reason |\n|:---:|---|---|\n" + "\n".join(
        f"| {r} | {c} | {why} |" for r, c, why in rows)


class MatchCompanyTest(unittest.TestCase):
    def test_exact_and_formatting_variants(self):
        self.assertEqual(m.match_company("Generali Italia S.p.A.", COMPANIES), "Generali Italia S.p.A.")
        self.assertEqual(m.match_company("**Generali Italia SpA**", COMPANIES), "Generali Italia S.p.A.")
        self.assertEqual(m.match_company("Crédit Agricole Vita", COMPANIES), "CRÈDIT AGRICOLE VITA")

    def test_abbreviation_matches_only_when_unambiguous(self):
        self.assertEqual(m.match_company("Poste Vita", COMPANIES), "Gruppo Assicurativo Poste Vita")
        self.assertIsNone(m.match_company("UniCredit", COMPANIES))  # two UniCredit companies

    def test_unknown_and_sentences_do_not_match(self):
        self.assertIsNone(m.match_company("Foo Insurance", COMPANIES))
        self.assertIsNone(m.match_company("Backed by the Generali Italia group", COMPANIES))


class ParseRankingTest(unittest.TestCase):
    def test_ranks_reasons_and_unmatched(self):
        text = "Here you go:\n\n" + table([
            ("**1**", "Generali Italia S.p.A.", "Largest insurer."),
            ("2", "Poste Vita", "Very accessible."),
            ("3", "Foo Insurance", "Not in the list."),
        ])
        p = m.parse_ranking(text, COMPANIES)
        self.assertEqual(p["ranks"], {"Generali Italia S.p.A.": 1, "Gruppo Assicurativo Poste Vita": 2})
        self.assertEqual(p["reasons"]["Gruppo Assicurativo Poste Vita"], "Very accessible.")
        self.assertEqual(len(p["unmatched"]), 1)

    def test_duplicate_keeps_first_placement(self):
        p = m.parse_ranking(table([("1", "ITAS VITA", "a"), ("2", "ITAS VITA", "b")]), COMPANIES)
        self.assertEqual(p["ranks"], {"ITAS VITA": 1})
        self.assertEqual(p["reasons"], {"ITAS VITA": "a"})

    def test_truncated_row_is_unmatched(self):
        p = m.parse_ranking(table([("1", "ITAS VITA", "a")]) + "\n| 2 | Vittoria Ass", COMPANIES)
        self.assertEqual(list(p["ranks"]), ["ITAS VITA"])
        self.assertEqual(p["unmatched"], ["| 2 | Vittoria Ass"])


class PersonaTest(unittest.TestCase):
    def test_default_is_every_txt_file_in_personas(self):
        personas = m.load_personas()
        self.assertEqual(personas, sorted(m.PERSONAS_DIR.glob("*.txt")))
        self.assertIn("young_family", [p.stem for p in personas])

    def test_explicit_personas_are_used_as_given(self):
        self.assertEqual(m.load_personas(["a.txt", "b.txt"]), [Path("a.txt"), Path("b.txt")])

    def test_every_persona_builds_a_complete_prompt(self):
        for path in m.load_personas():
            prompt = m.build_prompt(path.read_text(encoding="utf-8"), COMPANIES)
            for c in COMPANIES:
                self.assertIn(f"- {c}\n", prompt)
            self.assertIn("| Rank | Company | Reason |", prompt)
            self.assertIn(f"rank {len(COMPANIES)} is the worst", prompt)


class AggregationTest(unittest.TestCase):
    def call(self, model, run, order, error=""):
        return {"provider": "x", "model": model, "run": run, "error": error,
                "total_cost_usd": 0.01, "unmatched": [],
                "ranks": {c: i for i, c in enumerate(order, 1)},
                "reasons": {c: f"{model} r{run} #{i}" for i, c in enumerate(order, 1)}}

    def test_mean_over_runs_and_models(self):
        a, b = COMPANIES[:2]
        calls = [self.call("m1", 1, [a, b]), self.call("m1", 2, [b, a]), self.call("m2", 1, [a, b])]
        stats = m.rank_stats(calls, [a, b], ["m1", "m2"])
        self.assertEqual(stats[a, "m1"], (1.5, 0.5, 2))
        rows = m.build_rank_table([a, b], ["m1", "m2"], stats)
        self.assertEqual(rows[0], {"company": a, "m1": 1.5, "m2": 1, "avg_rank": 1.25})
        reasons = m.build_reasons_table(rows, ["m1", "m2"], calls)
        self.assertEqual(reasons[0]["m1"], "[run 1] m1 r1 #1\n[run 2] m1 r2 #2")
        self.assertEqual(reasons[0]["m2"], "m2 r1 #1")

    def test_save_writes_all_outputs(self):
        calls = [self.call("m1", 1, COMPANIES),
                 {**self.call("m2", 1, []), "error": "RuntimeError: boom"}]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            m.save(out, calls=calls, companies=COMPANIES, model_names=["m1", "m2"], runs=1,
                   persona_path="personas/young_family.txt", prompt="p",
                   started=datetime.now(timezone.utc))
            names = sorted(p.name for p in out.iterdir())
            self.assertEqual(names, ["long.csv", "rankings.csv", "raw.json", "reasons.csv", "report.md"])
            report = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("RuntimeError: boom", report)
            self.assertIn("CRÈDIT AGRICOLE VITA", report)


if __name__ == "__main__":
    unittest.main()
