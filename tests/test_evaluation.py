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


class ReportVisaLinkTests(unittest.TestCase):
    def test_report_links_the_visa_page_only_when_it_exists(self):
        import shutil
        import tempfile
        from pathlib import Path
        from scripts.build_report import build
        evaluation = Path(__file__).resolve().parents[1] / "reports/evaluation"
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "evaluation"
            src.mkdir()
            for f in evaluation.glob("*.json"):
                shutil.copy(f, src / f.name)
            shutil.copytree(evaluation.parent / "safety", Path(d) / "safety")
            without = Path(build(src, Path(d) / "a.html")).read_text(encoding="utf-8")
            (Path(d) / "visa").mkdir()
            (Path(d) / "visa" / "index.html").write_text("<!doctype html>", encoding="utf-8")
            with_page = Path(build(src, Path(d) / "b.html")).read_text(encoding="utf-8")
        self.assertNotIn('id="visa-link"', without)
        self.assertIn('id="visa-link"', with_page)
        self.assertIn('href="visa/index.html"', with_page)


def _visa_record(root):
    """A complete VisA evidence record built on the generated stand-in with three stand-in scorers."""
    import numpy as np
    from mizan.core.contract import digest
    from mizan.visa import PROTOCOL, build_manifest, read_split
    from mizan.visa_evaluation import Scorer, evaluate_visa
    from tests.visa_fixture import make_visa_tree
    feats = lambda im: np.asarray(im.convert("L").resize((12, 12)), dtype=np.float64)[..., None] / 255  # noqa: E731
    scorers = [Scorer("descriptor", feats, True, "test/d"), Scorer("dinov2-engine", lambda im: feats(im) ** 2, True, "test/e"),
               Scorer("dinov2-native", lambda im: feats(im) + .01, False, "test/n")]
    manifest = build_manifest(root, read_split(make_visa_tree(root, per_class=8)), "a" * 64, "b" * 64)
    record = evaluate_visa(root, manifest, scorers, quick=True)
    record.update(gate={"passed": True, "fixture": {"checked": ["x"] * 323}, "dinov2": {"checked": ["y"] * 404, "bit_exact": True}},
                  repeat_run={"identical": True, "process": "a second Python process"}, seal_commit="0" * 40, quick=False,
                  protocol_digest=digest(PROTOCOL), elapsed_seconds=60.0,
                  environment={"python": "3.12", "numpy": "2", "scipy": "1", "pillow": "13", "torch": "2", "cpu": "test"})
    return record


class VisaPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        import tempfile
        from pathlib import Path
        from scripts.build_visa_report import build
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.record = _visa_record(cls.dir / "data")
        (cls.dir / "basira.json").write_text(json.dumps(cls.record), encoding="utf-8")
        cls.pages = {p.name: p.read_text(encoding="utf-8") for p in build(cls.dir, cls.dir)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def embedded(self, name):
        import json
        import re
        return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', self.pages[name], re.S).group(1))

    def test_visa_pages_are_small_latin_and_offline(self):
        import re
        from scripts.build_site import EXTERNAL
        self.assertEqual(set(self.pages), {"index.html", "classes.html"})
        for name, page in self.pages.items():
            self.assertLess(len(page), 60000, name)
            self.assertIsNone(re.search("[֐-ࣿיִ-﷿ﹰ-﻿]", page), name)
            self.assertFalse([p.pattern for p in EXTERNAL if p.search(page)], name)
            self.assertNotIn("%%", page, name)

    def test_visa_summary_embeds_the_source_counts(self):
        data = self.embedded("index.html")
        for scorer in ("descriptor", "dinov2-engine", "dinov2-native"):
            for rule in ("as_shipped", "full", "full_pac"):
                src = self.record["pooled"][scorer][rule]
                self.assertEqual(data["pooled"][scorer][rule]["det"][:2], [src["detection"]["x"], src["detection"]["n"]])
                self.assertEqual(data["pooled"][scorer][rule]["fa"][:2], [src["false_alert"]["x"], src["false_alert"]["n"]])
        classes = self.embedded("classes.html")   # grouped by VisA type
        splits = self.record["splits"]
        self.assertEqual([c for c, _ in classes["classes"]], sorted(splits, key=lambda c: (splits[c]["type"], c)))
        # The protocol's numbers come from the record's settings, never typed into the page.
        settings = self.record["settings"]
        for name in ("index.html", "classes.html"):
            cfg = self.embedded(name)["cfg"]
            self.assertEqual((cfg["as_shipped"], cfg["full"]), tuple([settings["configurations"][k]["reference"], settings["configurations"][k]["calibration"]]
                                                                      for k in ("as_shipped", "full")))
            self.assertEqual((cfg["alpha"], cfg["delta"]), ("5%", "0.1"))
        self.assertEqual(self.embedded("classes.html")["cfg"]["draws"], settings["study"]["draws"])
        for text in ("10 reference", "30 reference", "δ = 0.1", "α = 5%", "400 calibration", "banks above"):
            for name, page in self.pages.items():
                self.assertNotIn(text, page, (name, text))

    def test_visa_summary_labels_the_pooled_false_alert_interval(self):
        # The spec: the Clopper–Pearson interval describes these fitted thresholds only, not the guarantee test.
        self.assertIn("fitted thresholds only", self.pages["index.html"])

    def test_visa_attribution_is_verbatim(self):
        from mizan.visa import ATTRIBUTION
        for name, page in self.pages.items():
            self.assertIn(ATTRIBUTION, page, name)
            self.assertIn('href="https://creativecommons.org/licenses/by/4.0/"', page, name)

    def test_visa_examples_are_four_images_or_none(self):
        examples = self.embedded("index.html")["examples"]
        self.assertEqual(set(examples), {"caught", "missed", "false_alert", "passed"})
        for value in examples.values():
            self.assertTrue(value is None or value[3].startswith("/9j/"), value)   # a base64 JPEG

    def test_visa_build_refuses_a_failed_gate_or_an_unrepeated_run(self):
        import copy
        import json
        import tempfile
        from pathlib import Path
        from scripts.build_visa_report import build
        for change in ({"gate": {"passed": False}}, {"repeat_run": {"identical": False, "process": "x"}}, {"repeat_run": None},
                       {"quick": True}, {"quick": {"split": [2, 2]}}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                bad = {**copy.deepcopy(self.record), **change}
                (Path(d) / "basira.json").write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    build(Path(d), Path(d))


def _opsd_record():
    """A complete OPSD evidence record on the stand-in file with the stub forecaster, marked as a full, gated,
    repeated run so the page builder accepts it."""
    import tempfile
    from pathlib import Path
    from mizan.core.contract import digest
    from mizan.opsd import PROTOCOL
    from mizan.opsd_evaluation import evaluate_opsd
    from tests.opsd_fixture import make_opsd_csv
    from tests.test_opsd_evaluation import stub_forecaster
    csv_path = make_opsd_csv(Path(tempfile.mkdtemp()) / "household.csv")
    record = evaluate_opsd(csv_path, stub_forecaster, quick=True)
    record.update(quick=False, gate={"passed": True, "ridge": {"checked": ["x"] * 253, "mismatches": []},
                                     "chronos": {"checked": ["y"] * 8, "mismatches": []}},
                  repeat_run={"identical": True, "process": "a second Python process", "differing_values": 0, "differences": []},
                  seal_commit="0" * 40, protocol_digest=digest(PROTOCOL), elapsed_seconds=60.0,
                  environment={"python": "3.12", "numpy": "2", "scipy": "1", "torch": "2", "cpu": "test"})
    return record


class OpsdPageTests(unittest.TestCase):
    FORBIDDEN = ("euro", "EUR", "€", "saving", "money")

    @classmethod
    def setUpClass(cls):
        import json
        import tempfile
        from pathlib import Path
        from scripts.build_opsd_report import build
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.record = _opsd_record()
        (cls.dir / "rasid.json").write_text(json.dumps(cls.record), encoding="utf-8")
        cls.page = build(cls.dir, cls.dir)[0].read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def embedded(self):
        import json
        import re
        return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', self.page, re.S).group(1))

    def source(self):
        import re
        return re.sub(r'<script id="data" type="application/json">.*?</script>', "", self.page, flags=re.S)

    def test_opsd_page_is_small_latin_and_offline(self):
        import re
        from scripts.build_site import EXTERNAL
        self.assertLess(len(self.page), 60000)
        self.assertIsNone(re.search("[֐-ࣿיִ-﷿ﹰ-﻿]", self.page))
        self.assertFalse([p.pattern for p in EXTERNAL if p.search(self.page)])
        self.assertNotIn("%%", self.page)

    def test_opsd_page_embeds_the_source_counts(self):
        data = self.embedded()
        for site, s in self.record["sites"].items():
            with self.subTest(site):
                self.assertEqual(data["sites"][site]["counts"]["target_days"], s["counts"]["target_days"])
                self.assertEqual(data["sites"][site]["plans"]["candidate"]["verified_feasible"], s["plans"]["candidate"]["verified_feasible"])
                self.assertEqual(data["sites"][site]["paired"][0]["reading"], s["paired"][0]["reading"])
        self.assertEqual(data["pooled"]["paired"][0]["reading"], self.record["pooled"]["paired"][0]["reading"])

    def test_opsd_attribution_and_wording(self):
        from mizan.opsd import ATTRIBUTION
        self.assertIn(ATTRIBUTION, self.page)
        self.assertIn('href="https://creativecommons.org/licenses/by/4.0/"', self.page)
        self.assertIn("reconstructed", self.page)
        self.assertIn("illustrative tariff units", self.page)
        import re
        text = re.sub(r"<style>.*?</style>", "", self.source(), flags=re.S)   # not the embedded font
        text = text.replace("Europe", "").replace("aylight-saving", "")      # the time zone and daylight-saving days
        for word in self.FORBIDDEN:
            self.assertNotIn(word, text)

    def test_opsd_build_refuses_a_failed_gate_an_unrepeated_run_or_a_quick_record(self):
        import copy
        import json
        import tempfile
        from pathlib import Path
        from scripts.build_opsd_report import build
        for change in ({"gate": {"passed": False}}, {"repeat_run": {"identical": False, "process": "x"}}, {"repeat_run": None},
                       {"quick": {"days_per_site": 10, "draws": 50}}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                bad = {**copy.deepcopy(self.record), **change}
                (Path(d) / "rasid.json").write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    build(Path(d), Path(d))

    def test_no_protocol_number_is_typed_into_the_page(self):
        source = self.source()
        for text in ("7-day", "7 days", "4000", "4,000", "0.35", "2026", "300 days", "2,048"):
            self.assertNotIn(text, source, text)
        cfg = self.embedded()["cfg"]
        self.assertEqual((cfg["block_days"], cfg["draws"], cfg["seed"], cfg["lookback_days"]), (7, 4000, 2026, 7))


class ReportOpsdLinkTests(unittest.TestCase):
    def test_report_links_the_opsd_page_only_when_it_exists(self):
        import shutil
        import tempfile
        from pathlib import Path
        from scripts.build_report import build
        evaluation = Path(__file__).resolve().parents[1] / "reports/evaluation"
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "evaluation"
            src.mkdir()
            for f in evaluation.glob("*.json"):
                shutil.copy(f, src / f.name)
            shutil.copytree(evaluation.parent / "safety", Path(d) / "safety")
            without = Path(build(src, Path(d) / "a.html")).read_text(encoding="utf-8")
            (Path(d) / "opsd").mkdir()
            (Path(d) / "opsd" / "index.html").write_text("<!doctype html>", encoding="utf-8")
            with_page = Path(build(src, Path(d) / "b.html")).read_text(encoding="utf-8")
        self.assertNotIn('id="opsd-link"', without)
        self.assertIn('id="opsd-link"', with_page)
        self.assertIn('href="opsd/index.html"', with_page)
