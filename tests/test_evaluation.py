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

    def test_report_summarizes_the_hugging_face_evaluation(self):
        import json, re, tempfile
        from pathlib import Path
        from scripts.build_report import build, summarize
        root = Path(__file__).resolve().parents[1]
        source = {k: json.loads((root / f"reports/hf/{k}.json").read_bytes()) for k in ("basira", "rasid", "sanad")}
        with tempfile.TemporaryDirectory() as d:
            page = Path(build(root / "reports/evaluation", Path(d) / "index.html")).read_text(encoding="utf-8")
            self.assertNotIn("hf", summarize(root / "reports/evaluation", hf_source=Path(d) / "missing"))
        self.assertIn("Foundation models from Hugging Face", page)
        self.assertIsNone(re.search("[֐-ࣿיִ-﷿ﹰ-﻿]", page), "the report page is Latin-only")
        self.assertLess(len(page), 60000, "the page carries summaries, not fixtures")
        hf = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))["hf"]
        # The figures are the files' own values.
        b, r, s = source["basira"], source["rasid"], source["sanad"]
        self.assertEqual(hf["basira"]["model"]["frozen_test"]["detected"], b["model"]["frozen_test"]["detected"])
        self.assertEqual(hf["basira"]["baseline"]["frozen_test"]["false_alerts"], b["baseline"]["frozen_test"]["false_alerts"])
        self.assertEqual(hf["basira"]["model"]["shipped"]["long_run_false_alert_estimate"],
                         b["model"]["calibration_study"]["shipped_calibration"]["long_run_false_alert_estimate"])
        self.assertEqual([[p[k] for k in ("policy", "reference", "mean_daily_difference_95ci")] for p in hf["rasid"]["paired"]],
                         [[p[k] for k in ("policy", "reference", "mean_daily_difference_95ci")] for p in r["model"]["paired_comparisons"]])
        # Pinball and CRPS are compared on the shared levels only, never with the shipped file's values.
        self.assertEqual(hf["rasid"]["levels"], r["baseline"]["forecast"]["shared_levels"])
        self.assertEqual(hf["rasid"]["forecast"]["pinball"], [r["baseline"]["forecast"]["ridge_mean_pinball"], r["model"]["forecast"]["chronos_mean_pinball"]])
        self.assertEqual(hf["rasid"]["forecast"]["crps"], [r["baseline"]["forecast"]["ridge_truncated_crps"], r["model"]["forecast"]["chronos_truncated_crps"]])
        for kind in ("bilingual", "en", "ar"):
            self.assertEqual(hf["sanad"]["e5"][kind], s["model"]["metrics"][kind]["all"])
            self.assertEqual(hf["sanad"]["lexical"][kind], s["baseline"]["metrics"][kind]["all"])
        self.assertEqual(hf["sanad"]["notes"], s["notes"])
        self.assertEqual([hf[k]["repo"] for k in ("basira", "rasid", "sanad")], [source[k]["identity"]["repo"] for k in ("basira", "rasid", "sanad")])
        # A summary: no per-item, per-day or gate records, and a small fraction of the three evidence files.
        keys = set()

        def walk(value):
            if isinstance(value, dict):
                keys.update(value)
                value = list(value.values())
            for item in value if isinstance(value, list) else ():
                walk(item)
        walk(hf)
        self.assertTrue(keys.isdisjoint({"rows", "origins", "checked", "bank_digest", "set_digest", "manifest_sha256"}), keys)
        self.assertLess(len(json.dumps(hf)), 6000)
