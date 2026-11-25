"""reports/hf/rasid_intervals.json: paired 95% intervals for the RASID accuracy comparisons, derived torch-free from the
per-origin values already in the evidence with the project's own bootstrap. The committed file must be exactly what its
inputs give, so it cannot drift from the evidence it is derived from."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mizan import evaluation, examples
from mizan.core.contract import canonical
from scripts import derive_hf_intervals as derive

ROOT = Path(__file__).resolve().parents[1]


class DerivedIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.committed = (ROOT / derive.OUT).read_bytes()
        cls.data = json.loads(cls.committed)
        cls.hf = json.loads((ROOT / derive.HF).read_bytes())
        cls.shipped = json.loads((ROOT / derive.SHIPPED).read_bytes())

    def test_the_committed_file_is_exactly_what_its_inputs_give(self):
        self.assertEqual(canonical(derive.derive()), self.committed)
        self.assertEqual(canonical(json.loads(self.committed)), self.committed, "canonical JSON, like the evidence files")

    def test_each_input_is_pinned_by_its_sha256(self):
        files = self.data["source"]["files"]
        self.assertEqual(set(files), {derive.HF, derive.SHIPPED})
        for path, sha in files.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), sha)

    def test_intervals_use_the_shipped_bootstrap_and_no_other(self):
        with mock.patch.object(evaluation, "paired_comparison", wraps=evaluation.paired_comparison) as bootstrap:
            result = derive.derive()
        self.assertEqual(bootstrap.call_count, len(result["comparisons"]))
        for call in bootstrap.call_args_list:
            self.assertEqual({k: call.kwargs[k] for k in ("block", "draws", "seed")}, {"block": 7, "draws": 4000, "seed": 2026})
        settings = self.data["source"]["bootstrap"]
        self.assertEqual((settings["block_days"], settings["draws"], settings["seed"]), (7, 4000, 2026))
        # The same method string as the paired cost intervals in the evidence file.
        self.assertEqual(settings["method"], self.hf["model"]["paired_comparisons"][0]["bootstrap"])

    def test_each_comparison_reads_its_own_series(self):
        got = {c["comparison"]: c for c in self.data["comparisons"]}
        fm, fb = self.hf["model"]["forecast"], self.hf["baseline"]["forecast"]
        means = {  # (mean of side a, mean of side b): the evidence files' own 60-day means
            "chronos_minus_ridge_load_mae": (fm["chronos_mae_kw"], fb["ridge_mae_kw"]),
            "chronos_minus_ridge_mean_pinball": (fm["chronos_mean_pinball"], fb["ridge_mean_pinball"]),
            "chronos_minus_ridge_truncated_crps": (fm["chronos_truncated_crps"], fb["ridge_truncated_crps"]),
            "chronos_minus_ridge_coverage_80_load": (fm["chronos_coverage_80_load"], fb["ridge_coverage_80_load"]),
            "ridge_minus_seasonal_naive_load_mae": (self.shipped["ridge_mae_kw"], self.shipped["seasonal_naive_mae_kw"]),
            "chronos_minus_chronos_risk_neutral_cost": tuple(self.hf["model"]["decision_quality"][k]["total_cost"] / 60 for k in ("chronos", "chronos_risk_neutral")),
            "candidate_minus_candidate_risk_neutral_cost": tuple(self.hf["baseline"]["decision_quality"][k]["total_cost"] / 60 for k in ("candidate", "candidate_risk_neutral")),
        }
        self.assertEqual(set(got), set(means))
        for name, (a, b) in means.items():
            c = got[name]
            with self.subTest(name):
                self.assertAlmostEqual(c["mean_a"], a, places=9)
                self.assertAlmostEqual(c["mean_b"], b, places=9)
                self.assertAlmostEqual(c["mean_difference"], a - b, places=9)
                lo, hi = c["interval_95"]
                self.assertLessEqual(lo, c["mean_difference"]); self.assertGreaterEqual(hi, c["mean_difference"])
                self.assertIs(c["resolved"], lo > 0 or hi < 0)
                self.assertEqual(c["days"], 60)
                self.assertTrue(c["unit"] and c["orientation"])

    def test_the_planner_facts_behind_the_risk_weighted_reading(self):
        planner = self.data["risk_weighted_planner"]
        settings = examples.settings(24)
        self.assertEqual((planner["risk_weight"], planner["cvar_beta"]), (settings["risk"], settings["beta"]))
        self.assertEqual(planner["chronos_scenario_weights"], self.hf["settings"]["scenario_weights"])
        # Each scenario weighs at least 1 - beta, so the worst (1 - beta) lies inside the costliest scenario alone.
        self.assertIs(planner["chronos_cvar_is_costliest_scenario"],
                      min(planner["chronos_scenario_weights"]) >= 1 - planner["cvar_beta"])

    def test_main_writes_the_same_bytes_readable_by_all(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "rasid_intervals.json"
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(derive.main(["--out", str(out)]), 0)
            self.assertEqual(printed.getvalue().strip(), str(out))
            self.assertEqual(out.read_bytes(), self.committed)
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
