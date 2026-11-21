"""SANAD grammar 2: Arabic coverage, operator-acknowledged context, profile upgrade."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mizan import examples
from mizan.core.contract import Refused, strict_load
from mizan.core.store import Broker
from mizan.sanad import engine as s
from mizan.service import Runner, drain, initialize, upgrade

ROOT = Path(__file__).resolve().parents[1]
RULE = [{"attribute": "apparent_power", "op": "ge", "threshold": "20", "unit": "kVA"}]


def verdict(pages, approval=1, rules=RULE, model="Atlas-20"):
    doc = {"name": "fixture", "pages": pages}
    payload = {"model": model, "documents": [doc], "rules": rules}
    profile = {"engine": s.PROFILE, "approved_sources": {s.source_id(doc): approval}}
    return s.evaluate(payload, profile)


def ack(page, line):
    start = page.index(line)
    return {"page": 1, "start": start, "end": start + len(line), "quote_sha256": s.quote_digest(line)}


class ArabicCoverageTests(unittest.TestCase):
    def check(self, line, expected, head="الطراز: Atlas-20"):
        got = verdict([head + "\n" + line])["requirements"][0]["verdict"]
        self.assertEqual(got, expected, line)

    def test_supported_arabic_typography_and_units(self):
        self.check("القدرة الظاهريـــة الاسمية: ٢٠ kVA", "MEETS")              # tatweel
        self.check("القدرة الظاهرية الاسمية: ٢٠ كيلو فولت أمبير", "MEETS")
        self.check("القدرة الظاهرية الاسمية: ١٦٠٠٠ فولت أمبير", "DOES_NOT_MEET")
        self.check("القدرة الظاهرية الاسمية: ٢٠٬٠٠٠ فولت أمبير", "MEETS")       # Arabic thousands separator
        self.check("القدرة الظاهرية الاسمية: ٢٠٫٥ kVA", "MEETS")                # Arabic decimal separator

    def test_ambiguous_or_foreign_numbers_abstain(self):
        for line in ["Rated apparent power: 20,000 VA",      # ASCII comma is locale-ambiguous
                     "Rated apparent power: 20,5 kVA",
                     "Rated apparent power: ২০ kVA",          # digits outside the declared scripts
                     "Rated apparent power: 2٬0000 VA"]:  # malformed grouping
            self.check(line, "REVIEW", head="Model: Atlas-20")

    def test_exact_second_conversion_in_both_languages(self):
        rules = [{"attribute": "runtime_100", "op": "ge", "threshold": "12", "unit": "min"}]
        for line in ["Runtime at 100% load: 720 s", "Runtime at 100% load: 720 seconds", "زمن التشغيل عند حمل ١٠٠٪: ٧٢٠ ثانية"]:
            with self.subTest(line=line):
                r = verdict(["Model: Atlas-20\n" + line], rules=rules)["requirements"][0]
                self.assertEqual((r["verdict"], r["evidence"][0]["value"]), ("MEETS", "12"))
        r = verdict(["Model: Atlas-20\nRuntime at 100% load: 719 s"], rules=rules)["requirements"][0]
        self.assertEqual(r["verdict"], "DOES_NOT_MEET")


class AcknowledgedContextTests(unittest.TestCase):
    PAGE = "UPS Technical Schedule\nPage 1 of 2\nModel: Atlas-20\nRated apparent power: 20 kVA"

    def test_unacknowledged_title_still_blocks(self):
        r = verdict([self.PAGE])["requirements"][0]
        self.assertEqual((r["verdict"], r["reason"]), ("REVIEW", "INCOMPLETE_CONTEXT"))

    def test_acknowledged_lines_unblock_and_are_reported(self):
        approval = {"level": 1, "acknowledged": [ack(self.PAGE, "UPS Technical Schedule"), ack(self.PAGE, "Page 1 of 2")]}
        result = verdict([self.PAGE], approval)
        r = result["requirements"][0]
        self.assertEqual(r["verdict"], "MEETS")
        self.assertEqual([g["quote"] for g in r["acknowledged_context"]], ["UPS Technical Schedule", "Page 1 of 2"])
        self.assertEqual(result["acknowledged_lines"], 2)
        # The acknowledgement is part of the evaluation identity.
        self.assertNotEqual(result["evaluation_digest"], verdict([self.PAGE])["evaluation_digest"])

    def test_partial_or_stale_acknowledgement_keeps_review(self):
        only_title = {"level": 1, "acknowledged": [ack(self.PAGE, "UPS Technical Schedule")]}
        self.assertEqual(verdict([self.PAGE], only_title)["requirements"][0]["verdict"], "REVIEW")
        stale = ack(self.PAGE, "UPS Technical Schedule")
        stale["quote_sha256"] = s.quote_digest("UPS Technical Schedule v0")
        approval = {"level": 1, "acknowledged": [stale, ack(self.PAGE, "Page 1 of 2")]}
        self.assertEqual(verdict([self.PAGE], approval)["requirements"][0]["verdict"], "REVIEW")

    def test_measurable_or_attribute_lines_cannot_be_acknowledged_even_if_signed(self):
        for line in ["Derated 10% above 40 °C", "Rated apparent power and runtime are guaranteed",
                     "Output limited to 16 kVA in eco mode", "القدرة الظاهرية الاسمية تقل عند ٤٠ درجة"]:
            with self.subTest(line=line):
                page = line + "\nModel: Atlas-20\nRated apparent power: 20 kVA"
                self.assertFalse(s.acknowledgeable(line))
                forged = {"level": 1, "acknowledged": [ack(page, line)]}
                self.assertEqual(verdict([page], forged)["requirements"][0]["verdict"], "REVIEW")

    def test_review_view_matches_parser_and_flags_eligibility(self):
        doc = {"name": "d", "pages": [self.PAGE + "\nDerated 10% above 40 °C\nTransfer time: about 2 ms"]}
        rows = {r["quote"]: r for r in s.review_document(doc)["lines"]}
        self.assertTrue(rows["UPS Technical Schedule"]["acknowledgeable"])
        self.assertFalse(rows["Derated 10% above 40 °C"]["acknowledgeable"])
        self.assertEqual(rows["Transfer time: about 2 ms"]["reason"], "UNSUPPORTED_VALUE_OR_CONDITION")
        self.assertFalse(rows["Transfer time: about 2 ms"]["acknowledgeable"])
        self.assertNotIn("Rated apparent power: 20 kVA", rows)

    def test_malformed_approvals_are_refused(self):
        for bad in [0, 4, True, "2", {"level": 2, "extra": 1}, {"level": 2, "acknowledged": [{"page": 1}]},
                    {"level": 2, "acknowledged": [{"page": 1, "start": 0, "end": 3, "quote_sha256": "x"}]}]:
            with self.subTest(approval=bad), self.assertRaises(Refused):
                verdict([self.PAGE], bad)


class ProfileUpgradeTests(unittest.TestCase):
    def test_previous_grammar_is_refused_with_an_actionable_code(self):
        old = {**examples.profile("sanad"), "engine": "sanad/closed-ups-grammar/1"}
        with self.assertRaises(Refused) as caught:
            s.evaluate(examples.procurement(), old)
        self.assertEqual(caught.exception.code, "PROFILE_UPGRADE_REQUIRED")

    def test_upgrade_preserves_approvals_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d, contextlib.closing(Broker(d)) as b:
            approvals = {"custom-source": 3, **examples.profile("sanad")["approved_sources"]}
            b.promote("local", "sanad", {"engine": "sanad/closed-ups-grammar/1", "approved_sources": approvals, "mode": "CLOSED_GRAMMAR"}, 0)
            initialize(b, "local")
            first = upgrade(b, "local")
            self.assertEqual(first["sanad"]["action"], "upgraded")
            self.assertEqual({first[k]["action"] for k in ("basira", "rasid")}, {"current"})
            dep = b.db.execute("SELECT seal FROM deployments WHERE tenant='local' AND subject='sanad'").fetchone()
            profile = b.cas.open(json.loads(dep[0]), "local", "profile", "sanad")
            self.assertEqual((profile["engine"], profile["approved_sources"]), (s.PROFILE, approvals))
            self.assertEqual(upgrade(b, "local")["sanad"]["action"], "current")
            job = b.submit("local", "operator", "sanad", "after-upgrade", examples.procurement())
            self.assertEqual(drain(b, Runner(b), "local", "operator", job)["state"], "SUCCEEDED")


class OperatorCliTests(unittest.TestCase):
    """approve-document → run → verify, through the real CLI and worker subprocess."""

    def cli(self, data, *args, ok=True):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        run = subprocess.run([sys.executable, "-m", "mizan", "--data", data, *args], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode == 0, ok, run.stderr)
        return json.loads(run.stdout) if ok else json.loads(run.stderr)

    def test_acknowledge_title_then_run_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            doc = {"name": "datasheet.pdf", "pages": ["Acme UPS datasheet\nDerated 10% above 40 °C\nModel: Atlas-20\nRated apparent power: 20 kVA"]}
            path = Path(d) / "doc.json"
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            data = str(Path(d) / "store")
            shown = self.cli(data, "approve-document", str(path))
            self.assertEqual([l["acknowledgeable"] for l in shown["lines_needing_review"]], [True, False])
            refused = self.cli(data, "approve-document", str(path), "--level", "2", "--acknowledge", "1:19", ok=False)
            self.assertEqual(refused["error"], "NOT_ACKNOWLEDGEABLE")
            # Only the title is acknowledged: the derating line must keep the verdict in REVIEW.
            approved = self.cli(data, "approve-document", str(path), "--level", "2", "--acknowledge-eligible")
            self.assertEqual((approved["acknowledged"], approved["unacknowledged_lines"]), (1, 1))
            payload = Path(d) / "payload.json"
            payload.write_text(json.dumps({"model": "Atlas-20", "documents": [doc], "rules": RULE}, ensure_ascii=False))
            out = Path(d) / "result.json"
            self.cli(data, "run", "sanad", str(payload), "--out", str(out))
            result = strict_load(out.read_bytes())
            self.assertEqual(result["result"]["requirements"][0]["verdict"], "REVIEW")
            self.assertEqual(result["result"]["acknowledged_lines"], 1)
            self.assertEqual(self.cli(data, "verify", str(out))["verified"], "job")


if __name__ == "__main__":
    unittest.main()
