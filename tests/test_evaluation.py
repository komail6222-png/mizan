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
        from scripts.build_report import build, models_summary, summarize
        root = Path(__file__).resolve().parents[1]
        source = {k: json.loads((root / f"reports/hf/{k}.json").read_bytes()) for k in ("basira", "rasid", "sanad")}
        intervals = json.loads((root / "reports/hf/rasid_intervals.json").read_bytes())
        with tempfile.TemporaryDirectory() as d:
            page = Path(build(root / "reports/evaluation", Path(d) / "index.html")).read_text(encoding="utf-8")
            models = (Path(d) / "models.html").read_text(encoding="utf-8")   # the comparison has its own page
            self.assertIsNone(models_summary(root / "reports/evaluation", hf_source=Path(d) / "missing"))
            # Like safety/, the HF evidence is the source folder's sibling: another source never borrows the repo's own.
            (Path(d) / "evaluation").mkdir()
            for k in ("sanad", "basira", "rasid"):
                (Path(d) / f"evaluation/{k}.json").write_bytes((root / f"reports/evaluation/{k}.json").read_bytes())
            self.assertIsNone(models_summary(Path(d) / "evaluation"))
            self.assertNotIn("mae_gap", summarize(Path(d) / "evaluation")["rasid"])
            build(Path(d) / "evaluation", Path(d) / "evaluation/index.html")
            self.assertFalse((Path(d) / "evaluation/models.html").exists())
            self.assertNotIn('href="models.html"', (Path(d) / "evaluation/index.html").read_text(encoding="utf-8"))
        # The report links to the models page and no longer carries the comparison itself.
        self.assertIn('<a href="models.html">', page)
        embedded = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))
        self.assertNotIn("hf", embedded)
        self.assertNotIn('id="hf-section"', page)
        # The 3.0 forecast-error comparison is stated with its derived paired interval.
        gap = {c["comparison"]: c for c in intervals["comparisons"]}["ridge_minus_seasonal_naive_load_mae"]
        self.assertEqual(embedded["rasid"]["mae_gap"], {"d": gap["mean_difference"], "ci": gap["interval_95"]})
        for p in (page, models):
            self.assertIsNone(re.search("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]", p), "the report pages are Latin-only")
            self.assertLess(len(p), 60000, "each page carries summaries, not fixtures")
            self.assertIsNone(re.search(r"""<(?:script|img|link|iframe)\b[^>]*\b(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", p, re.I), "no external loads")
            self.assertNotIn("url(http", p)
        page = models
        self.assertIn("Foundation models from Hugging Face", page)
        self.assertIn('<section id="hf-section"', page)
        self.assertIn('href="index.html"', page, "the models page links back to the report")
        hf = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))["hf"]
        # Accuracy differences, premiums and the planner facts come from the derived intervals, unchanged.
        derived = {c["comparison"]: c for c in intervals["comparisons"]}
        self.assertEqual([[a["k"], a["d"], a["ci"]] for a in hf["rasid"]["accuracy"]],
                         [[k, derived["chronos_minus_ridge_" + k]["mean_difference"], derived["chronos_minus_ridge_" + k]["interval_95"]]
                          for k in ("load_mae", "mean_pinball", "truncated_crps", "coverage_80_load")])
        self.assertEqual(hf["rasid"]["premium"]["chronos"]["ci"], derived["chronos_minus_chronos_risk_neutral_cost"]["interval_95"])
        self.assertEqual(hf["rasid"]["planner"]["costliest"], intervals["risk_weighted_planner"]["chronos_cvar_is_costliest_scenario"])
        # The risk-weighted tail rows and SANAD's recall@1 by datasheet language are the files' own values.
        r, s = source["rasid"], source["sanad"]
        self.assertEqual(hf["rasid"]["tail"], {"cvar": [r["baseline"]["decision_quality"]["candidate"]["mean_worst_10pct_days"], r["model"]["decision_quality"]["chronos"]["mean_worst_10pct_days"]],
                                               "worst": [r["baseline"]["decision_quality"]["candidate"]["worst_day_cost"], r["model"]["decision_quality"]["chronos"]["worst_day_cost"]]})
        for name, side in (("lexical", "baseline"), ("e5", "model")):
            self.assertEqual(hf["sanad"]["lang"][name], [s[side]["metrics"][q][d]["recall_at_1"] for q, d in (("en", "en"), ("en", "ar"), ("ar", "ar"), ("ar", "en"))])
        # The figures are the files' own values.
        b, r, s = source["basira"], source["rasid"], source["sanad"]
        self.assertEqual(hf["basira"]["model"]["frozen_test"]["detected"], b["model"]["frozen_test"]["detected"])
        self.assertEqual(hf["basira"]["baseline"]["frozen_test"]["false_alerts"], b["baseline"]["frozen_test"]["false_alerts"])
        for side in ("baseline", "model"):
            shipped = b[side]["calibration_study"]["shipped_calibration"]
            self.assertEqual(hf["basira"][side]["shipped"], {"m": shipped["m"], "rate": shipped["long_run_false_alert_estimate"],
                                                              "p_worse": shipped["probability_of_a_bank_at_least_this_bad"]})
        self.assertEqual([[p[k] for k in ("policy", "reference", "mean_daily_difference_95ci")] for p in hf["rasid"]["paired"]],
                         [[p[k] for k in ("policy", "reference", "mean_daily_difference_95ci")] for p in r["model"]["paired_comparisons"]])
        # Pinball and CRPS are compared on the shared levels only, never with the shipped file's values.
        self.assertEqual(hf["rasid"]["levels"], r["baseline"]["forecast"]["shared_levels"])
        self.assertEqual(hf["rasid"]["forecast"]["pinball"], [r["baseline"]["forecast"]["ridge_mean_pinball"], r["model"]["forecast"]["chronos_mean_pinball"]])
        self.assertEqual(hf["rasid"]["forecast"]["crps"], [r["baseline"]["forecast"]["ridge_truncated_crps"], r["model"]["forecast"]["chronos_truncated_crps"]])
        for kind in ("bilingual", "en", "ar"):  # [recall@1, recall@3, MRR@4]
            self.assertEqual(hf["sanad"]["e5"][kind], [s["model"]["metrics"][kind]["all"][m] for m in ("recall_at_1", "recall_at_3", "mrr_at_4")])
            self.assertEqual(hf["sanad"]["lexical"][kind], [s["baseline"]["metrics"][kind]["all"][m] for m in ("recall_at_1", "recall_at_3", "mrr_at_4")])
        self.assertEqual(hf["sanad"]["notes"], s["notes"])
        # Wording thresholds come from the files: the study's stated precision and the gate's solver tolerance.
        self.assertIn(f"±{hf['basira']['precision_points']} percentage points", b["baseline"]["calibration_study"]["scope"])
        self.assertEqual(hf["rasid"]["shipped_gap"], max(r["gate"]["total_cost_relative_difference"][k] for k in ("candidate", "candidate_risk_neutral")))
        self.assertEqual(hf["rasid"]["tolerance"], r["settings"]["gate_tolerances"]["candidate_totals_relative"])
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

    def test_intervals_derived_from_other_evidence_stop_the_build(self):
        import json, shutil, tempfile
        from pathlib import Path
        from scripts.build_report import models_summary, summarize
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(root / "reports/hf", Path(d) / "hf")
            shutil.copytree(root / "reports/evaluation", Path(d) / "evaluation", ignore=shutil.ignore_patterns("*.html", "release-2.1"))
            self.assertIn("hf", models_summary(Path(d) / "evaluation"))   # an exact copy builds
            for path in ("reports/hf/rasid.json", "reports/evaluation/rasid.json"):
                with self.subTest(path=path):
                    data = json.loads((root / "reports/hf/rasid_intervals.json").read_bytes())
                    data["source"]["files"][path] = "0" * 64
                    (Path(d) / "hf/rasid_intervals.json").write_text(json.dumps(data))
                    with self.assertRaisesRegex(SystemExit, "derive_hf_intervals"):
                        models_summary(Path(d) / "evaluation") if path.startswith("reports/hf") else summarize(Path(d) / "evaluation")
