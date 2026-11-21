"""Proof-rendered explanations and executed counterfactuals (release 3.0)."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from mizan import examples
from mizan.basira import engine as vision
from mizan.explain import explain
from mizan.rasid import engine as energy
from mizan.sanad import engine as s

ROOT = Path(__file__).resolve().parents[1]
QUOTES = re.compile(r"“([^”]*)”|«([^»]*)»")


class SanadExplanationTests(unittest.TestCase):
    def test_every_quoted_phrase_exists_verbatim_in_the_documents(self):
        payload, profile = examples.procurement(), examples.profile("sanad")
        e = explain("sanad", s.evaluate(payload, profile), payload, profile)
        corpus = "\n".join(p for d in payload["documents"] for p in d["pages"])
        quoted = [a or b for text in e["en"] + e["ar"] for a, b in QUOTES.findall(text)]
        self.assertGreater(len(quoted), 8)
        for q in quoted:
            self.assertIn(q, corpus, "an explanation quoted text that is not in any document")
        self.assertEqual(len(e["en"]), len(e["ar"]), "both languages explain every requirement")

    def test_counterfactuals_reproduce_when_rerun_independently(self):
        payload, profile = examples.procurement(), examples.profile("sanad")
        e = explain("sanad", s.evaluate(payload, profile), payload, profile)
        by_attr = {x["rule"]["attribute"]: x["counterfactuals"] for x in e["counterfactuals"]}
        policy = by_attr["apparent_power"][0]
        self.assertEqual((policy["verdict"], policy["actor"]), ("MEETS", "operator policy"))
        again = s.evaluate({**payload, "policy": "highest_authority", "compare_policies": False}, profile)
        self.assertEqual(again["requirements"][0]["verdict"], "MEETS")
        relax = by_attr["efficiency_100"][0]
        self.assertIn("≥ 94 %", relax["change"])
        rules = copy.deepcopy(payload["rules"])
        rules[2]["threshold"] = "94"
        self.assertEqual(s.evaluate({**payload, "rules": rules}, profile)["requirements"][2]["verdict"], "MEETS")

    def test_acknowledgement_and_transcript_counterfactuals(self):
        page = "UPS Technical Schedule\nDerated 10% above 40 °C\nModel: Atlas-20\nRated apparent power: 20 kVA"
        doc = {"name": "sheet", "pages": [page]}
        payload = {"model": "Atlas-20", "documents": [doc], "rules": [{"attribute": "apparent_power", "op": "ge", "threshold": "20", "unit": "kVA"}]}
        profile = {"engine": s.PROFILE, "approved_sources": {s.source_id(doc): 1}}
        cfs = explain("sanad", s.evaluate(payload, profile), payload, profile)["counterfactuals"][0]["counterfactuals"]
        self.assertEqual([c["verdict"] for c in cfs], [None])          # one measurable line: not acknowledgeable
        self.assertIn("Correct the transcript", cfs[0]["change"])
        doc["pages"] = ["UPS Technical Schedule\nModel: Atlas-20\nRated apparent power: 20 kVA"]
        profile = {"engine": s.PROFILE, "approved_sources": {s.source_id(doc): 1}}
        cfs = explain("sanad", s.evaluate(payload, profile), payload, profile)["counterfactuals"][0]["counterfactuals"]
        self.assertEqual((cfs[0]["verdict"], cfs[0]["actor"]), ("MEETS", "operator only"))

    def test_unapproved_document_counterfactual_is_operator_only(self):
        payload = examples.procurement()
        payload["documents"] = [{"name": "agent upload", "pages": ["Model: Atlas-20\nRated apparent power: 20 kVA"]}]
        payload["rules"] = payload["rules"][:1]
        profile = examples.profile("sanad")
        cfs = explain("sanad", s.evaluate(payload, profile), payload, profile)["counterfactuals"][0]["counterfactuals"]
        approve = [c for c in cfs if "approves" in c["change"]]
        self.assertEqual((approve[0]["verdict"], approve[0]["actor"]), ("MEETS", "operator only"))

    def test_without_documents_only_the_explanation_is_given(self):
        payload, profile = examples.procurement(), examples.profile("sanad")
        e = explain("sanad", s.evaluate(payload, profile))
        self.assertIsNone(e["counterfactuals"])
        self.assertIn("note", e)


class VisionAndEnergyExplanationTests(unittest.TestCase):
    def test_basira_counterfactuals_follow_the_decision_rule(self):
        small = vision.evaluate(examples.inspection(4), examples.profile("basira"))
        e = explain("basira", small)
        self.assertIn("smallest attainable p-value is 1/5", e["en"][0])
        calibrate = [c for c in e["counterfactuals"] if "Calibrate" in c["change"]][0]
        self.assertEqual(calibrate["verdict"], "FLAG")
        self.assertIn("19", calibrate["change"])
        flagged = vision.evaluate(examples.inspection(), examples.profile("basira"))
        below = explain("basira", flagged)["counterfactuals"][0]
        self.assertEqual(below["verdict"], "REVIEW")
        self.assertEqual(vision.rank_decision(flagged["calibration_scores"], flagged["threshold"], ".05")["decision"], "REVIEW")

    def test_rasid_counterfactuals_are_resolved_and_reverified(self):
        r = energy.evaluate(examples.energy(), examples.profile("rasid"))
        e = explain("rasid", r)
        self.assertIn("VERIFIED_FEASIBLE", e["en"][0])
        changes = {c["change"].split(" (")[0]: c for c in e["counterfactuals"]}
        self.assertEqual(set(changes), {"Risk weight 0", "Battery capacity +25%", "Battery power +25%"})
        for c in changes.values():
            self.assertTrue(c["status"].startswith("VERIFIED"))
        # Removing the risk weight cannot raise expected cost; it buys back the tail premium.
        self.assertLessEqual(changes["Risk weight 0"]["expected_cost_delta"], 1e-6)
        self.assertLessEqual(changes["Battery capacity +25%"]["objective_delta"], 1e-6)

    def test_cli_explains_a_verified_output_with_counterfactuals_from_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            run = lambda *a: subprocess.run([sys.executable, "-m", "mizan", "--data", d, *a], cwd=ROOT, env=env,
                                            capture_output=True, text=True, timeout=120)
            self.assertEqual(run("run", "sanad", "examples/sanad.json", "--out", f"{d}/out.json").returncode, 0)
            done = run("explain", f"{d}/out.json", "--lang", "ar")
            self.assertEqual(done.returncode, 0, done.stderr)
            e = json.loads(done.stdout)
            self.assertNotIn("en", e)
            self.assertTrue(e["ar"][0].startswith("القدرة الظاهرية الاسمية"))
            self.assertIsNotNone(e["counterfactuals"])
            tampered = json.loads(Path(f"{d}/out.json").read_text())
            tampered["result"]["requirements"][0]["verdict"] = "MEETS"
            Path(f"{d}/bad.json").write_text(json.dumps(tampered, ensure_ascii=False))
            self.assertNotEqual(run("explain", f"{d}/bad.json").returncode, 0, "a tampered result must not be explained")


if __name__ == "__main__":
    unittest.main()
