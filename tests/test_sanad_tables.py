"""SANAD grammar 3: delimited multi-model tables with cell-level provenance."""
import unittest

from mizan.core.contract import Refused
from mizan.sanad import engine as s

RULE = {"attribute": "apparent_power", "op": "ge", "threshold": "20", "unit": "kVA"}
TABLE = ("| Model | Atlas-10 | Atlas-20 | Atlas-30 |\n|---|---|---|---|\n"
         "| Rated apparent power | 10 kVA | {} | 30 kVA |\n| Transfer time | 0 ms | 0 ms | 2 ms |")


def evaluate(page, model="Atlas-20", approval=1, rules=(RULE,)):
    doc = {"name": "table", "pages": [page]}
    payload = {"model": model, "documents": [doc], "rules": list(rules)}
    return s.evaluate(payload, {"engine": s.PROFILE, "approved_sources": {s.source_id(doc): approval}})


def verdict(page, **kw):
    return evaluate(page, **kw)["requirements"][0]["verdict"]


class TableBindingTests(unittest.TestCase):
    def test_offered_column_decides_and_neighbours_never_leak(self):
        self.assertEqual(verdict(TABLE.format("20 kVA")), "MEETS")
        self.assertEqual(verdict(TABLE.format("16 kVA")), "DOES_NOT_MEET")   # 10 and 30 sit next to it
        self.assertEqual(verdict(TABLE.format("16 kVA"), model="Atlas-30"), "MEETS")
        self.assertEqual(verdict(TABLE.format("16 kVA"), model="Atlas-10"), "DOES_NOT_MEET")
        self.assertEqual(verdict(TABLE.format("at least 21 kVA")), "MEETS")
        self.assertEqual(verdict(TABLE.format("optional 29 kVA")), "REVIEW")

    def test_claim_carries_row_cell_and_header_spans(self):
        page = TABLE.format("20 kVA")
        claim = evaluate(page)["requirements"][0]["evidence"][0]
        self.assertEqual(page[claim["start"]:claim["end"]], claim["quote"])
        self.assertEqual(page[claim["cell"]["start"]:claim["cell"]["end"]], "20 kVA")
        self.assertEqual(claim["cell"]["column"], "atlas-20")
        header = claim["table_header"]
        self.assertEqual(page[header["start"]:header["end"]], "| Model | Atlas-10 | Atlas-20 | Atlas-30 |")

    def test_unit_column_tabs_and_right_to_left_extraction(self):
        page = "Model\tUnit\tAtlas-20\tAtlas-30\nRated apparent power\tkVA\t20\t30"
        claim = evaluate(page)["requirements"][0]["evidence"][0]
        self.assertEqual((claim["value"], claim["cell"]["text"], claim["unit_cell"]["text"]), ("20", "20", "kVA"))
        self.assertEqual(page[claim["unit_cell"]["start"]:claim["unit_cell"]["end"]], "kVA")
        rtl = "| Atlas-30 | Atlas-20 | الطراز |\n| ١٦ kVA | ٢٠ kVA | القدرة الظاهرية الاسمية |"
        self.assertEqual(verdict(rtl), "MEETS")
        self.assertEqual(verdict(rtl, model="Atlas-30"), "DOES_NOT_MEET")

    def test_malformed_tables_abstain(self):
        cases = {
            "empty cell": TABLE.format("—"),
            "footnote marker": TABLE.format("20 kVA*"),
            "extra cell": "| Model | Atlas-10 | Atlas-20 |\n| Rated apparent power | 10 kVA | 20 kVA | 30 kVA |",
            "missing cell": "| Model | Atlas-10 | Atlas-20 |\n| Rated apparent power | 20 kVA |",
            "duplicate model": "| Model | Atlas-20 | Atlas-20 |\n| Rated apparent power | 10 kVA | 20 kVA |",
            "unusable model id": "| Model | Atlas 20 | Atlas-30 |\n| Rated apparent power | 20 kVA | 30 kVA |",
            "no header": "| Rated apparent power | 20 kVA |",
            "unit twice": "| Model | Unit | Atlas-20 |\n| Rated apparent power | kVA | 20 kVA |",
        }
        for name, page in cases.items():
            with self.subTest(case=name):
                self.assertEqual(verdict(page), "REVIEW")

    def test_table_for_other_models_is_ignored_and_prose_after_a_table_needs_a_heading(self):
        other = "Model: Atlas-20\nRated apparent power: 20 kVA\n| Model | Atlas-30 |\n| Rated apparent power | 30 kVA |"
        self.assertEqual(verdict(other), "MEETS")
        trailing = TABLE.format("20 kVA") + "\nRated apparent power: 16 kVA"
        self.assertEqual(verdict(trailing), "REVIEW")
        reheaded = TABLE.format("20 kVA") + "\nModel: Atlas-20\nTransfer time: 0 ms"
        self.assertEqual(verdict(reheaded), "MEETS")

    def test_unscored_rows_block_until_acknowledged_and_related_rows_cannot_be(self):
        row = "| Input voltage | 230 V | 400 V | 400 V |"
        page = TABLE.format("20 kVA") + "\n" + row
        self.assertEqual(verdict(page), "REVIEW")
        start = page.index(row)
        ack = {"page": 1, "start": start, "end": start + len(row), "quote_sha256": s.quote_digest(row)}
        self.assertEqual(verdict(page, approval={"level": 1, "acknowledged": [ack]}), "MEETS")
        related = "| Efficiency (eco mode) | 99 % | 99 % | 99 % |"
        page = TABLE.format("20 kVA") + "\n" + related
        start = page.index(related)
        forged = {"page": 1, "start": start, "end": start + len(related), "quote_sha256": s.quote_digest(related)}
        self.assertEqual(verdict(page, approval={"level": 1, "acknowledged": [forged]}), "REVIEW")

    def test_review_view_classifies_table_rows(self):
        doc = {"name": "r", "pages": [TABLE.format("—") + "\n| Input voltage | 230 V | 400 V | 400 V |"]}
        rows = {r["quote"].split("|")[1].strip(): r for r in s.review_document(doc)["lines"]}
        self.assertEqual(rows["Rated apparent power"]["reason"], "UNSUPPORTED_VALUE_OR_CONDITION")
        self.assertFalse(rows["Rated apparent power"]["acknowledgeable"])
        self.assertTrue(rows["Input voltage"]["acknowledgeable"])
        self.assertNotIn("Transfer time", rows)  # every model's cell parses

    def test_grammar_2_profile_is_refused(self):
        doc = {"name": "t", "pages": [TABLE.format("20 kVA")]}
        with self.assertRaises(Refused) as caught:
            s.evaluate({"model": "Atlas-20", "documents": [doc], "rules": [RULE]},
                       {"engine": "sanad/closed-ups-grammar/2", "approved_sources": {}})
        self.assertEqual(caught.exception.code, "PROFILE_UPGRADE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
