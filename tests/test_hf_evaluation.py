"""Evaluation harnesses with injectable components, checked against the shipped results (torch-free)."""
import json
import unittest
from pathlib import Path

from mizan import evaluation, hf_evaluation as hf
from mizan.basira import engine as b

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = json.loads((ROOT / "reports/evaluation/basira.json").read_text())
QUICK = dict(draws=3, sizes=(24,), pools=(60, 60, 20))


class BasiraHarnessTests(unittest.TestCase):
    def test_descriptor_reproduces_shipped_frozen_test(self):
        r = hf.basira_hf(b.features, b.DESCRIPTOR, draws=3, sizes=(24,), pools=(60, 60, 20))
        self.assertEqual((r["frozen_test"]["detected"], r["frozen_test"]["false_alerts"]), (40, 8))
        self.assertEqual(r["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])

    def test_gate_reports_each_mismatch(self):
        bad = {"frozen_test": {"detected": 40, "false_alerts": 9, "bank_digest": SHIPPED["bank_digest"]}, "calibration_study": SHIPPED["calibration_study"]}
        g = hf.basira_gate(bad, SHIPPED)
        self.assertFalse(g["passed"]); self.assertIn("false_alerts", g["mismatches"])

    def test_harness_mirrors_the_shipped_functions_item_for_item(self):
        r = hf.basira_hf(b.features, b.DESCRIPTOR, **QUICK)
        self.assertEqual(r["profile"], b.DESCRIPTOR)
        self.assertEqual(r["patches_averaged"], 4)  # top 2.5% of the 12 x 12 descriptor grid
        t = r["frozen_test"]
        self.assertEqual((t["test_items"], t["calibration_m"]), (SHIPPED["test_items"], SHIPPED["calibration_m"]))
        self.assertEqual([(x["seed"], x["condition"], x["p_value"], x["flag"]) for x in t["rows"]],
                         [(x["seed"], x["condition"], x["p_value"], x["flag"]) for x in SHIPPED["rows"]])
        self.assertEqual(r["calibration_study"], evaluation.calibration_study(**QUICK))
        # A reduced run never passes the gate: only its calibration study differs from the shipped full-size one.
        g = hf.basira_gate(r, SHIPPED)
        self.assertFalse(g["passed"]); self.assertIn("calibration_study.pools", g["mismatches"])
        self.assertTrue(all(n.startswith(("calibration_study.", "shipped_calibration.", "rows[")) for n in g["mismatches"]), g["mismatches"])

    def test_every_scored_image_goes_through_the_given_feature_function(self):
        calls = []
        def counted(im):
            calls.append(1); return b.features(im)
        r = hf.basira_hf(counted, b.DESCRIPTOR, **QUICK)
        # 10 reference + 24 calibration images in fit, 80 frozen items, then every pool image.
        self.assertEqual(len(calls), 10 + 24 + 80 + sum(QUICK["pools"]))
        self.assertEqual(r["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])
        other = hf.basira_hf(b.features, "test/another-profile", **QUICK)
        self.assertEqual(other["profile"], "test/another-profile")
        self.assertNotEqual(other["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])
        self.assertEqual(other["calibration_study"]["bank_digest"], other["frozen_test"]["bank_digest"])

    def test_gate_passes_the_shipped_values_and_names_every_check(self):
        g = hf.basira_gate(self.shipped_as_result(), SHIPPED)
        self.assertTrue(g["passed"]); self.assertEqual(g["mismatches"], [])
        # 3 frozen counts + 80 frozen rows x 4 fields + 6 study settings + 7 shipped-calibration fields + 32 row fields.
        self.assertEqual(len(g["checked"]), 368); self.assertEqual(len(set(g["checked"])), 368)
        self.assertEqual(g["checked"][:3], ["detected", "false_alerts", "bank_digest"])
        self.assertEqual([n for n in g["checked"] if n.startswith("frozen_test.rows[seed=9179].")],
                         ["frozen_test.rows[seed=9179]." + k for k in ("seed", "condition", "p_value", "flag")])
        self.assertIn("shipped_calibration.long_run_false_alert_estimate", g["checked"])
        self.assertIn("rows[m=100].pac_share_of_banks_above_alpha", g["checked"])
        # A row field that is absent on one side is a mismatch, even where the other side holds null.
        study = json.loads(json.dumps(SHIPPED["calibration_study"])); del study["rows"][0]["pac_rank"]
        g = hf.basira_gate({**self.shipped_as_result(), "calibration_study": study}, SHIPPED)
        self.assertEqual(g["mismatches"], ["rows[m=24].pac_rank"])

    def test_gate_fails_when_two_frozen_items_swap_flags(self):
        same = self.shipped_as_result(); rows = same["frozen_test"]["rows"]
        hit = next(x for x in rows[:40] if x["flag"]); miss = next(x for x in rows[:40] if not x["flag"])
        hit["flag"], miss["flag"] = False, True  # the false-alert count is unchanged
        g = hf.basira_gate(same, SHIPPED)
        self.assertFalse(g["passed"])
        self.assertEqual(sorted(g["mismatches"]), sorted(f"frozen_test.rows[seed={x['seed']}].flag" for x in (hit, miss)))

    @staticmethod
    def shipped_as_result():
        """The shipped numbers in the harness's result shape (a fresh copy each call)."""
        frozen = {k: SHIPPED[k] for k in ("detected", "false_alerts", "bank_digest", "rows")}
        return json.loads(json.dumps({"frozen_test": frozen, "calibration_study": SHIPPED["calibration_study"]}))


if __name__ == "__main__":
    unittest.main()
