"""Release 2.3 evaluation machinery: RASID decision quality and BASIRA calibration study helpers."""
import unittest

from mizan import evaluation


class DecisionQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluation.energy_evaluation(days=3)

    def test_every_policy_is_solved_verified_and_summarized(self):
        q = self.report["decision_quality"]
        self.assertEqual(set(q), set(evaluation.POLICIES))
        for name, row in q.items():
            self.assertEqual(row["verified_feasible_plans"], 3, name)
        self.assertEqual(q["perfect_foresight"]["regret_vs_perfect_foresight"], 0)
        self.assertEqual(q["perfect_foresight"]["share_of_achievable_battery_value"], 1)
        self.assertEqual(q["no_battery"]["share_of_achievable_battery_value"], 0)
        self.assertGreater(q["no_battery"]["total_cost"], q["perfect_foresight"]["total_cost"])
        # 2.1 summary keys are preserved for comparisons with earlier reports.
        self.assertEqual(self.report["candidate_total_cost"], q["candidate"]["total_cost"])

    def test_paired_comparison_interval_brackets_its_estimate(self):
        for c in self.report["paired_comparisons"]:
            lo, hi = c["mean_daily_difference_95ci"]
            self.assertLessEqual(lo, c["mean_daily_difference"] + 1e-9)
            self.assertGreaterEqual(hi, c["mean_daily_difference"] - 1e-9)
            self.assertEqual(c["days_cheaper"] + c["days_more_expensive"] <= 3, True)

    def test_tail_mean_uses_the_worst_share(self):
        self.assertEqual(evaluation.tail_mean([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), 10)
        self.assertEqual(evaluation.tail_mean(list(range(1, 21))), 19.5)


if __name__ == "__main__":
    unittest.main()


class ReportPageTests(unittest.TestCase):
    def test_report_embeds_only_summary_data_and_escapes_markup(self):
        import json, re, tempfile
        from pathlib import Path
        from scripts.build_report import build, summarize
        root = Path(__file__).resolve().parents[1] / "reports/evaluation"
        summary = summarize(root)
        self.assertEqual([s["cases"] for s in summary["sanad"]], [36, 16, 16])
        self.assertEqual(len(summary["rasid"]["policies"]), 5)
        with tempfile.TemporaryDirectory() as d:
            page = Path(build(root, Path(d) / "index.html")).read_text(encoding="utf-8")
        self.assertNotIn("%%DATA%%", page)
        self.assertNotIn("eval(", page)
        embedded = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertNotIn("<", embedded)
        self.assertEqual(json.loads(embedded)["basira"]["minimum_m"], 45)
        self.assertLess(len(page), 60000, "the page carries summaries, not fixtures")
