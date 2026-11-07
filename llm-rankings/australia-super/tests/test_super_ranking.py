"""Offline tests: input checks, fund-name matching, parsing and fees. No API calls.

Run from this study's folder (llm-rankings/australia-super):
    python -m unittest discover tests
"""

import io
import json
import math
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_super_ranking as m  # noqa: E402

FUNDS = m.load_funds()
OCTOBER = m.RESULTS / "20261026T020224Z" / "mid_career" / "raw.json"  # the run in the README
HEADER = "| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |"


def row(rank, fund, admin="$100", invest="$500", trans="$50", total="$650", reason="Why."):
    return f"| {rank} | {fund} | {admin} | {invest} | {trans} | {total} | {reason} |"


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def file(self, name, content):
        path = self.tmp / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path


class InputFilesTest(unittest.TestCase):
    """The committed inputs pass every check."""

    def test_inputs_are_utf8_and_every_fund_has_fees(self):
        for name in ["companies.csv", "fees.csv"]:
            (m.INPUTS / name).read_bytes().decode("utf-8")  # raises if not UTF-8
        fees = m.load_fees(FUNDS.funds)
        self.assertEqual(list(fees.index), FUNDS.funds)

    def test_every_persona_reads(self):
        for path in sorted(m.PERSONAS.glob("*.txt")):
            balance, text = m.read_persona(path)
            self.assertGreater(balance, 0)
            self.assertNotIn("balance:", text)


class MatchTest(unittest.TestCase):
    def test_exact_ignoring_case_spacing_markdown_and_brackets(self):
        self.assertEqual(FUNDS.match("**AustralianSuper**"), "AustralianSuper")
        self.assertEqual(FUNDS.match("Australian Super"), "AustralianSuper")
        self.assertEqual(FUNDS.match("Legal Super"), "legalsuper")
        self.assertEqual(FUNDS.match("HOSTPLUS Superannuation Fund (High Growth)"), "HOSTPLUS Superannuation Fund")

    def test_aliases(self):
        self.assertEqual(FUNDS.match("Aware Super"), "MySuper Lifecycle")
        self.assertEqual(FUNDS.match("ING Living Super"), "OneSuper ING")

    def test_shortened_name_only_when_unambiguous(self):
        self.assertEqual(FUNDS.match("HOSTPLUS"), "HOSTPLUS Superannuation Fund")  # Claude, October 2026
        self.assertEqual(FUNDS.match("Cbus"), "Cbus Super")
        self.assertIsNone(FUNDS.match("Super"))
        self.assertIsNone(FUNDS.match("Super Fund"))  # generic words only

    def test_misspelling_needs_a_distinctive_word_in_common(self):
        self.assertEqual(FUNDS.match("Hostplus Superanuation Fund"), "HOSTPLUS Superannuation Fund")
        self.assertEqual(FUNDS.match("Vanguard Super"), "Vanguard MySuper")
        self.assertIsNone(FUNDS.match("Acme Super"))
        # "Aware Super" is about 75% similar to "REI Super" but is a different fund
        self.assertIsNone(m.FundNames({"REI Super": [], "Prime Super": []}).match("Aware Super"))

    def test_two_funds_cannot_share_a_name(self):
        with self.assertRaises(SystemExit):
            m.FundNames({"Aware Super": [], "MySuper Lifecycle": ["AWARE super"]})


class ParseTest(unittest.TestCase):
    def test_rows_dollars_duplicates_and_unused_rows(self):
        text = "\n".join([
            "Here is my ranking:", "", HEADER, "|---|---|---|---|---|---|---|",
            row("**1**", "**Essential Super**", "$1,234", "~$300–400", "unknown", "$1,534"),
            row(2, "HOSTPLUS"),
            row(3, "Acme Super"),               # not in the list
            row(4, "Essential Super"),          # repeated: the first row is kept
            "| 5 | Cbus Super | $100 |",        # cut off
        ])
        funds, unused = m.parse(text, FUNDS)
        self.assertEqual(list(funds), ["Essential Super", "HOSTPLUS Superannuation Fund"])
        self.assertEqual(funds["Essential Super"], {"rank": 1, "est_admin": 1234.0, "est_invest": 300.0,
                                                    "est_trans": None, "est_total": 1534.0, "reason": "Why."})
        self.assertEqual(unused, [row(3, "Acme Super"), "| 5 | Cbus Super | $100 |"])

    def test_every_october_2026_answer_now_covers_all_funds(self):
        """Two of Claude's five answers covered 29 funds, because of "HOSTPLUS"."""
        for call in json.loads(OCTOBER.read_text(encoding="utf-8"))["calls"]:
            with self.subTest(model=call["model"], run=call["run"]):
                funds, unused = m.parse(call["response"], FUNDS)
                self.assertEqual(len(funds), len(FUNDS.funds))
                self.assertEqual(unused, [])


class PersonaTest(TempDirTest):
    def test_header_gives_the_balance_and_is_not_sent(self):
        balance, text = m.read_persona(self.file("p.txt", "balance: $150,000\n---\nYou have $150,000 in super.\n"))
        self.assertEqual((balance, text), (150_000, "You have $150,000 in super."))

    def test_windows_line_endings(self):
        balance, text = m.read_persona(self.file("p.txt", "balance: 150000\r\n---\r\nI have 150000 dollars.\r\n"))
        self.assertEqual((balance, text), (150_000, "I have 150000 dollars."))

    def test_problems_stop_the_run(self):
        cases = {
            "no header": "You have $150,000 in super.",
            "text does not state it": "balance: 150000\n---\nYou have $150k in super.",
            "text states another amount": "balance: 150000\n---\nYou have $1,150,000 in super.",
            "not a number": "balance: lots\n---\nYou have $150,000 in super.",
            "misspelt field": "balence: 150000\n---\nYou have $150,000 in super.",
        }
        for case, content in cases.items():
            with self.subTest(case), self.assertRaises(SystemExit):
                m.read_persona(self.file("p.txt", content))


class FeesTest(TempDirTest):
    def test_actual_fees_on_150k(self):
        actual = m.actual_fees(m.load_fees(FUNDS.funds), 150_000)
        self.assertEqual(actual.loc["Essential Super"].tolist(), [1, 360.0, 120.0, 240.0, 0.0])
        self.assertEqual(actual.loc["HOSTPLUS Superannuation Fund", "actual_total"], 1263.0)

    def test_every_problem_is_listed(self):
        path = self.file("fees.csv", "\n".join([
            "company,admin_fee_fixed_aud,admin_fee_rate,investment_fee_rate,transaction_cost_rate",
            "A,50,0.50%,0.005,0.001",
            "B,-10,0.001,0.5,0.001",
            "B,50,0.001,0.005,0.001",
            "D,50,0.001,,0.001",
        ]))
        with self.assertRaises(SystemExit) as caught:
            m.load_fees(["A", "B", "C", "D"], path)
        message = str(caught.exception)
        for expected in ["B appears more than once", "C is in companies.csv but not in fees.csv",
                         "A: admin_fee_rate is '0.50%', not a number; rates are decimals",
                         "B: admin_fee_fixed_aud is negative", "B: investment_fee_rate is 0.5, above 5%",
                         "D: investment_fee_rate is '', not a number"]:
            self.assertIn(expected, message)

    def test_file_not_in_utf8_is_refused(self):
        path = self.file("companies.csv", b"company\r\nTeam Super\xa0\r\n")  # as saved by Excel before the fix
        with self.assertRaises(SystemExit) as caught:
            m.load_funds(path)
        self.assertIn("CSV UTF-8", str(caught.exception))

    def test_pct_error_is_blank_when_actual_is_zero(self):
        err = m.abs_pct_err(pd.Series([150.0, 110.0]), pd.Series([0.0, 100.0]))
        self.assertTrue(math.isnan(err[0]))
        self.assertEqual(err[1], 10.0)


class ReplayTest(TempDirTest):
    def test_october_2026_answers_through_the_whole_script(self):
        """Run main() on the saved October 2026 answers instead of calling the APIs."""
        saved = json.loads(OCTOBER.read_text(encoding="utf-8"))
        answers = {(c["model"], c["run"]): c for c in saved["calls"]}
        asked: dict[str, int] = {}

        def replay(model, prompt, max_tokens):
            self.assertEqual(prompt, saved["prompt"])  # the models would see exactly the same prompt
            asked[model] = asked.get(model, 0) + 1
            call = answers[model, asked[model]]
            return call["response"], call["input_tokens"], call["output_tokens"]

        personas = self.tmp / "personas"
        personas.mkdir()
        shutil.copy(m.PERSONAS / "mid_career.txt", personas)
        with mock.patch.dict(m.CALLERS, {p: replay for p in m.CALLERS}), \
                mock.patch.object(m, "PERSONAS", personas), redirect_stdout(io.StringIO()):
            m.main(["--runs", "5", "--output-dir", str(self.tmp / "results")])

        out = next((self.tmp / "results").glob("*/mid_career"))
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["accuracy.csv", "agreement.csv", "long.csv", "raw.json", "summary.csv"])
        accuracy = pd.read_csv(out / "accuracy.csv", index_col="model", encoding="utf-8-sig")
        self.assertTrue((accuracy.funds_ranked_per_run == 30).all())
        long = pd.read_csv(out / "long.csv", encoding="utf-8-sig")
        self.assertFalse(np.isinf(long.select_dtypes("number")).any().any())
        self.assertTrue(long.loc[long.actual_trans == 0, "abs_pct_err_trans"].isna().all())


if __name__ == "__main__":
    unittest.main()
