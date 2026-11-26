"""The introduction page (scripts/build_site.py): every figure is read from reports/, and the wording it fills is guarded."""
import copy
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from mizan.core.contract import strict_load
from scripts import build_site

ROOT = Path(__file__).resolve().parents[1]
HF = {k: json.loads((ROOT / f"reports/hf/{k}.json").read_bytes()) for k in ("basira", "rasid", "sanad")}
INTERVALS = json.loads((ROOT / "reports/hf/rasid_intervals.json").read_bytes())


def hf_facts(basira=None, rasid=None, sanad=None, intervals=None):
    return build_site.hf_facts(basira or HF["basira"], rasid or HF["rasid"], sanad or HF["sanad"], intervals or INTERVALS)


def with_derived(name, **fields):
    """The derived intervals with one comparison changed."""
    intervals = copy.deepcopy(INTERVALS)
    for c in intervals["comparisons"]:
        if c["comparison"] == name:
            c.update(fields)
    return intervals


def with_interval(lo, hi):
    rasid = copy.deepcopy(HF["rasid"])
    for p in rasid["model"]["paired_comparisons"]:
        if (p["policy"], p["reference"]) == ("chronos_risk_neutral", "candidate_risk_neutral"):
            p["mean_daily_difference_95ci"] = [lo, hi]
    return rasid


class FoundationModelFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = strict_load((ROOT / "reports/demo/demo_results.json").read_bytes())
        cls.figures = build_site.facts(cls.packet)

    def test_facts_read_each_model_next_to_the_current_component(self):
        expected = {  # the stored values in reports/hf/, as the report page rounds them
            "hf_basira_false": "1", "hf_basira_false_base": "8", "hf_basira_intact": "40",
            "hf_basira_detected": "40", "hf_basira_defects": "40", "hf_basira_m": "24", "hf_basira_draws": "400",
            "hf_rasid_mae": "2.572", "hf_rasid_mae_ridge": "2.794", "hf_rasid_days": "60",
            # Accuracy differences, each with its paired interval from reports/hf/rasid_intervals.json
            "hf_rasid_mae_gap": "−0.221", "hf_rasid_mae_gap_lo": "−0.698", "hf_rasid_mae_gap_hi": "+0.118",
            "hf_rasid_mae_gap_phrase_en": "not resolved", "hf_rasid_mae_gap_phrase_ar": "غير مؤكد",
            "hf_rasid_cov": "65.1%", "hf_rasid_cov_ridge": "74.7%",
            "mae_gap": "−0.274", "mae_gap_lo": "−0.559", "mae_gap_hi": "+0.056",
            "mae_gap_phrase_en": "not resolved", "mae_gap_phrase_ar": "غير مؤكد", "rasid_days": "60",
            # SANAD recall@1 by (query label, datasheet language)
            "hf_sanad_e5_r1_en_en": "85.8%", "hf_sanad_lexical_r1_en_en": "100.0%",
            "hf_sanad_e5_r1_ar_ar": "77.5%", "hf_sanad_lexical_r1_ar_ar": "94.0%",
            "hf_sanad_e5_r1_en_ar": "69.9%", "hf_sanad_lexical_r1_en_ar": "23.7%",
            "hf_sanad_e5_r1_ar_en": "67.4%", "hf_sanad_lexical_r1_ar_en": "25.1%",
            "hf_rasid_value": "−0.135", "hf_rasid_lo": "−0.507", "hf_rasid_hi": "+0.154",
            "hf_rasid_phrase_en": "not resolved", "hf_rasid_phrase_ar": "غير مؤكد",
            "hf_rasid_weighted": "+1.060", "hf_rasid_weighted_lo": "+0.328", "hf_rasid_weighted_hi": "+1.641",
            "hf_sanad_lexical_r1": "96.9%", "hf_sanad_e5_r1": "80.5%",
            "hf_sanad_lexical_r1_en": "61.1%", "hf_sanad_e5_r1_en": "77.7%",
            "hf_sanad_lexical_r1_ar": "60.2%", "hf_sanad_e5_r1_ar": "72.5%",
        }
        self.assertEqual({k: self.figures.get(k) for k in expected}, expected)
        self.assertEqual(hf_facts(), {k: v for k, v in self.figures.items() if k.startswith("hf_")})
        # Study rows are paired by calibration size, not by position.
        basira = copy.deepcopy(HF["basira"])
        basira["model"]["calibration_study"]["rows"].reverse()
        self.assertEqual(hf_facts(basira=basira), hf_facts())

    def test_test_counts_are_read_from_the_record_and_fail_closed(self):
        record = (ROOT / "reports/application-tests.txt").read_text(encoding="utf-8")
        counts = build_site.recorded_tests(record)
        self.assertEqual({k: v for k, v in self.figures.items() if k.startswith("tests")}, counts)
        total, skipped = int(re.search(r"^Ran (\d+) tests", record, re.M).group(1)), int(re.search(r"^OK \(skipped=(\d+)\)$", record, re.M).group(1))
        self.assertEqual(counts, {"tests": str(total), "tests_run": str(total - skipped), "tests_skipped": str(skipped), "tests_hf": str(skipped)})
        hf_line = re.search(r"^# \.venv-hf result: .*$", record, re.M).group(0)
        py313_line = re.search(r"^# Python 3\.13\.\d+ result.*$", record, re.M).group(0)
        broken = {  # each record the introduction's sentence would misdescribe
            "the main run failed": record.replace(f"\nOK (skipped={skipped})\n", f"\nFAILED (failures=1, skipped={skipped})\n"),
            "a skip that is not a model-loader test": record.replace(" ... skipped 'optional HF runtime'", " ... skipped 'FastAPI missing'", 1),
            "the skipped tests were not run elsewhere": record.replace(hf_line + "\n", ""),
            "fewer tests passed in the HF environment than were skipped": record.replace(hf_line, hf_line.replace(f"Ran {skipped} tests", f"Ran {skipped - 1} tests")),
            "the HF environment run failed": record.replace(hf_line, hf_line.replace(": OK", ": FAILED")),
            "no Python 3.13 run": record.replace(py313_line + "\n", ""),
            "a different suite on Python 3.13": record.replace(py313_line, py313_line.replace(f"Ran {total} tests", f"Ran {total - 1} tests")),
        }
        for name, text in broken.items():
            with self.subTest(name), self.assertRaises(SystemExit):
                self.assertNotEqual(text, record)
                build_site.recorded_tests(text)

    def test_rasid_phrase_follows_the_sign_of_the_interval(self):
        for (lo, hi), en, ar in [((-0.5, 0.2), "not resolved", "غير مؤكد"), ((0.1, 0.4), "resolved", "مؤكد"),
                                 ((-0.4, -0.1), "resolved", "مؤكد"), ((0.0, 0.3), "not resolved", "غير مؤكد")]:
            with self.subTest(interval=(lo, hi)):
                f = hf_facts(rasid=with_interval(lo, hi))
                self.assertEqual((f["hf_rasid_phrase_en"], f["hf_rasid_phrase_ar"]), (en, ar))
                self.assertEqual((f["hf_rasid_lo"], f["hf_rasid_hi"]), (("+" if lo >= 0 else "−") + f"{abs(lo):.3f}", ("+" if hi >= 0 else "−") + f"{abs(hi):.3f}"))

    def test_accuracy_phrases_follow_the_sign_of_the_derived_interval(self):
        for (lo, hi), en, ar in [((-0.7, 0.1), "not resolved", "غير مؤكد"), ((-0.7, -0.1), "resolved", "مؤكد"),
                                 ((0.1, 0.4), "resolved", "مؤكد"), ((-0.3, 0.0), "not resolved", "غير مؤكد")]:
            with self.subTest(interval=(lo, hi)):
                f = hf_facts(intervals=with_derived("chronos_minus_ridge_load_mae", interval_95=[lo, hi], resolved=lo > 0 or hi < 0))
                self.assertEqual((f["hf_rasid_mae_gap_phrase_en"], f["hf_rasid_mae_gap_phrase_ar"]), (en, ar))
                self.assertEqual((f["hf_rasid_mae_gap_lo"], f["hf_rasid_mae_gap_hi"]), (build_site.signed(lo, 3), build_site.signed(hi, 3)))
        # A derived file whose "resolved" flag disagrees with its own interval is refused.
        with self.assertRaisesRegex(SystemExit, "resolved=True"):
            hf_facts(intervals=with_derived("chronos_minus_ridge_load_mae", resolved=True))

    def test_derived_intervals_must_come_from_the_evidence_as_it_is(self):
        build_site.check_derived(INTERVALS)   # the committed file
        stale = copy.deepcopy(INTERVALS)
        stale["source"]["files"]["reports/hf/rasid.json"] = "0" * 64
        with self.assertRaisesRegex(SystemExit, "derive_hf_intervals"):
            build_site.check_derived(stale)

    def test_the_3_0_error_interval_must_be_that_of_the_shipped_errors(self):
        real = build_site.load

        def load(path):  # the derived ridge and seasonal-naive errors no longer match reports/evaluation/rasid.json
            data = real(path)
            if path == build_site.INTERVALS:
                for c in data["comparisons"]:
                    if c["comparison"] == "ridge_minus_seasonal_naive_load_mae":
                        c["mean_a"] += 0.01
            return data
        with mock.patch.object(build_site, "load", load), mock.patch.object(build_site, "check_derived", lambda intervals: None), \
                self.assertRaisesRegex(SystemExit, "ridge and seasonal-naive errors"):
            build_site.facts(self.packet)

    def test_wording_that_no_longer_matches_the_evidence_stops_the_build(self):
        def change(kind, path, value):
            data = copy.deepcopy(HF[kind])
            node = data
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            return {kind: data}
        cases = {  # each change must trip its own guard
            "E5 no longer ahead with the English label": (change("sanad", ["model", "metrics", "en", "all", "recall_at_1"], 0.5), "SANAD"),
            "E5 no longer behind with the bilingual query": (change("sanad", ["model", "metrics", "bilingual", "all", "recall_at_1"], 0.99), "SANAD"),
            "study means further apart than the stated precision": (change("basira", ["model", "calibration_study", "rows", 1, "marginal_mean_false_alert"], 0.045), "roughly equal"),
            "the two sides no longer detect the same defects": (change("basira", ["model", "frozen_test", "detected"], 39), "same defects"),
            "the two sides no longer have the same intact count": (change("basira", ["model", "frozen_test", "rows", 0, "condition"],
                                                                        "defect" if HF["basira"]["model"]["frozen_test"]["rows"][0]["condition"] == "intact" else "intact"), "same frozen items"),
            # RASID accuracy: stated only with the derived intervals, which must belong to this rasid.json.
            "a rasid.json the intervals were not derived from (MAE)": (change("rasid", ["model", "forecast", "chronos_mae_kw"], 2.9), "not derived from this"),
            "a rasid.json the intervals were not derived from (CRPS)": (change("rasid", ["model", "forecast", "chronos_truncated_crps"], 1.2), "not derived from this"),
            "a rasid.json the intervals were not derived from (coverage)": (change("rasid", ["baseline", "forecast", "ridge_coverage_80_load"], 0.8), "not derived from this"),
            "pinball difference now resolved": ({"intervals": with_derived("chronos_minus_ridge_mean_pinball", interval_95=[0.01, 0.13], resolved=True)}, "pinball-loss and CRPS"),
            "CRPS difference now resolved": ({"intervals": with_derived("chronos_minus_ridge_truncated_crps", interval_95=[-0.3, -0.01], resolved=True)}, "pinball-loss and CRPS"),
            "coverage difference no longer resolved": ({"intervals": with_derived("chronos_minus_ridge_coverage_80_load", interval_95=[-0.16, 0.01], resolved=False)}, "fewer hours|less often"),
            "coverage now resolved higher": ({"intervals": with_derived("chronos_minus_ridge_coverage_80_load", interval_95=[0.01, 0.1], resolved=True)}, "fewer hours|less often"),
            "risk-weighted Chronos plan no longer resolved costlier": (change("rasid", ["model", "paired_comparisons", 2, "mean_daily_difference_95ci"], [-0.1, 1.6]), "risk-weighted"),
            # SANAD by datasheet language: behind in the query's own language, ahead only across languages.
            "E5 no longer behind on English datasheets with the English label": (change("sanad", ["model", "metrics", "en", "en", "recall_at_1"], 1.0), "own language"),
            "E5 no longer behind on Arabic datasheets with the Arabic label": (change("sanad", ["model", "metrics", "ar", "ar", "recall_at_1"], 0.95), "own language"),
            "E5 no longer ahead with the English label on Arabic datasheets": (change("sanad", ["model", "metrics", "en", "ar", "recall_at_1"], 0.2), "other language"),
            "E5 no longer ahead with the Arabic label on English datasheets": (change("sanad", ["model", "metrics", "ar", "en", "recall_at_1"], 0.25), "other language"),
            "a gate that did not pass": (change("rasid", ["gate", "passed"], False), "rasid.json: .*gate passed"),
            "a repeat run that was not identical": (change("sanad", ["repeat_run", "identical"], False), "sanad.json: .*repeat run was identical"),
            "a study scope without its stated precision": (change("basira", ["baseline", "calibration_study", "scope"], "Generated rings only."), "precision"),
            "study sizes that no longer match": (change("basira", ["model", "calibration_study", "rows", 2, "m"], 200), "calibration sizes"),
        }
        for name, (changed, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(SystemExit, message):
                hf_facts(**changed)

    def test_landing_shows_the_block_with_both_sides_in_both_languages(self):
        page, _ = build_site.landing_html(self.packet, self.figures)
        block = re.search(r'<div class="models" id="models">(.*?)<!-- /models -->', page, re.S)
        self.assertIsNotNone(block, "the Foundation models block is missing")
        for lang, heading in [("en", "Foundation models, measured honestly"), ("ar", "نماذج تأسيسية، قيست بأمانة")]:
            spans = "".join(re.findall(rf'<span data-l="{lang}">(.*?)</span>', block.group(1), re.S))
            with self.subTest(lang=lang):
                self.assertIn(heading, spans)
                # Every model figure stands next to its current-component counterpart, in each language.
                for model, current in [("hf_basira_false", "hf_basira_false_base"), ("hf_rasid_mae", "hf_rasid_mae_ridge"),
                                       ("hf_sanad_e5_r1", "hf_sanad_lexical_r1"), ("hf_sanad_e5_r1_en", "hf_sanad_lexical_r1_en"),
                                       ("hf_sanad_e5_r1_ar", "hf_sanad_lexical_r1_ar")]:
                    figures = block.group(1) if model in ("hf_basira_false", "hf_rasid_mae", "hf_sanad_e5_r1") else spans
                    self.assertIn(self.figures[model], figures, model)
                    self.assertIn(self.figures[current], figures, current)
                for key in ("hf_rasid_value", "hf_rasid_lo", "hf_rasid_hi", f"hf_rasid_phrase_{lang}",
                            "hf_rasid_weighted", "hf_rasid_weighted_lo", "hf_rasid_weighted_hi",
                            # every accuracy difference stands with its interval and its phrase
                            "hf_rasid_mae_gap", "hf_rasid_mae_gap_lo", "hf_rasid_mae_gap_hi", f"hf_rasid_mae_gap_phrase_{lang}",
                            "hf_rasid_cov", "hf_rasid_cov_ridge"):
                    self.assertIn(self.figures[key], spans, key)
                # SANAD: the one-label gain is shown by datasheet language, with both sides of each pair.
                for q, d in (("en", "en"), ("ar", "ar"), ("en", "ar"), ("ar", "en")):
                    for side in ("e5", "lexical"):
                        self.assertIn(self.figures[f"hf_sanad_{side}_r1_{q}_{d}"], spans, (side, q, d))
                self.assertNotRegex(spans, r"more accurate|score worse|better forecast|built on them")
        self.assertIn('href="report/models.html"', block.group(1))
        self.assertNotIn("#hf-section", page, "the Hugging Face comparison moved to its own page")
        models = (ROOT / "reports/evaluation/models.html").read_text(encoding="utf-8")
        self.assertIn('<section id="hf-section"', models, "the link's target exists on the models page")
        self.assertIn("report/models.html", build_site.COPIED, "the site serves the models page where the link points")

    def test_the_3_0_forecast_line_states_its_interval(self):
        page, _ = build_site.landing_html(self.packet, self.figures)
        case = re.search(r'<div class="case"><h4>RASID</h4>(.*?)</div>', page, re.S).group(1)
        for lang in ("en", "ar"):
            spans = "".join(re.findall(rf'<span data-l="{lang}">(.*?)</span>', case, re.S))
            with self.subTest(lang=lang):
                for key in ("mae_ridge", "mae_naive", "mae_gap", "mae_gap_lo", "mae_gap_hi", f"mae_gap_phrase_{lang}", "rasid_days"):
                    self.assertIn(self.figures[key], spans, key)
        self.assertNotIn("A better forecast", case)
        self.assertNotIn("تنبؤ أدق", case)

    def test_landing_states_the_test_counts_and_keeps_arabic_figures_left_to_right(self):
        page, _ = build_site.landing_html(self.packet, self.figures)
        row = re.search(r"<li>((?:(?!</li>).)*application-tests\.txt</a></li>)", page, re.S).group(1)
        for lang in ("en", "ar"):
            spans = "".join(re.findall(rf'<span data-l="{lang}">(.*?)</span>', row, re.S))
            with self.subTest(lang=lang):
                self.assertIn(self.figures["tests_run"], spans)
                self.assertIn(self.figures["tests_hf"], spans)
        # In Arabic prose a signed figure or a percentage sits in a left-to-right isolate, so its sign stays left of
        # the digits and its % on the right, wherever the line breaks.
        arabic = "".join(re.findall(r'<span data-l="ar">(.*?)</span>', page, re.S))
        loose = re.sub(r'<bdi dir="ltr">[^<]*</bdi>', "", arabic)
        self.assertEqual(re.findall(r"[+−]\d[\d.,]*|\d[\d.,]*%", loose), [])
        self.assertIn('<bdi dir="ltr">', arabic)


VISA = json.loads((ROOT / "reports/visa/basira.json").read_bytes())


def visa_changed(*edits):
    """The VisA record with each (path, value) edit applied; a path step may be a paired comparison's name."""
    record = copy.deepcopy(VISA)
    for path, value in edits:
        node = record
        for key in path[:-1]:
            node = next(c for c in node if c["comparison"] == key) if isinstance(node, list) else node[key]
        node[path[-1]] = value
    return record


class RealImageFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = strict_load((ROOT / "reports/demo/demo_results.json").read_bytes())
        cls.figures = build_site.facts(cls.packet)

    def test_facts_read_the_real_image_evaluation(self):
        expected = {  # the stored values in reports/visa/basira.json, as the results page rounds them
            "visa_classes": "12", "visa_test_normals": "962", "visa_test_anomalies": "1,200",
            "visa_alpha": "5%", "visa_full_reference": "30", "visa_full_calibration": "100",
            "visa_desc_detect": "26.8%", "visa_desc_detect_lo": "24.3%", "visa_desc_detect_hi": "29.4%",
            "visa_desc_fa": "5.6%", "visa_desc_fa_lo": "4.2%", "visa_desc_fa_hi": "7.3%",
            "visa_native_detect": "38.6%", "visa_native_detect_lo": "35.8%", "visa_native_detect_hi": "41.4%",
            "visa_exch_en": "within the exact range the calibration promise implies",
            "visa_exch_ar": "ضمن المدى الدقيق الذي يقتضيه وعد المعايرة",
            "visa_auroc_desc": "0.717", "visa_auroc_desc_lo": "0.696", "visa_auroc_desc_hi": "0.736",
            "visa_auroc_native": "0.831", "visa_auroc_native_lo": "0.814", "visa_auroc_native_hi": "0.847",
        }
        self.assertEqual({k: v for k, v in self.figures.items() if k.startswith("visa_")}, expected)
        # The same figures, computed here from the record itself.
        full = {s: VISA["pooled"][s]["full"] for s in ("descriptor", "dinov2-native")}
        computed = {
            "visa_classes": str(len(VISA["splits"])),
            "visa_test_normals": build_site.group(sum(s["test_normal"] for s in VISA["splits"].values())),
            "visa_test_anomalies": build_site.group(sum(s["test_anomaly"] for s in VISA["splits"].values())),
            **{f"visa_{k}_{m}{suffix}": build_site.percent(v) for k, s, m in (("desc", "descriptor", "detect"), ("desc", "descriptor", "fa"),
                                                                             ("native", "dinov2-native", "detect"))
               for suffix, v in zip(("", "_lo", "_hi"), (lambda p: (p["rate"], *p["ci95"]))(full[s]["detection" if m == "detect" else "false_alert"]))},
            **{f"visa_auroc_{k}{suffix}": f"{v:.3f}" for k, s in (("desc", "descriptor"), ("native", "dinov2-native"))
               for suffix, v in zip(("", "_lo", "_hi"), (VISA["auroc"][s]["mean"], *VISA["auroc"][s]["mean_ci95"]))},
        }
        self.assertEqual({k: self.figures[k] for k in computed}, computed)

    def test_exchangeability_phrase_follows_the_verdict(self):
        path = ["exchangeability", "descriptor", "full", "pooled"]
        lo, hi = VISA["exchangeability"]["descriptor"]["full"]["pooled"]["range95"]
        for observed, verdict, en in [(hi + 1, "more", "above"), (lo - 1, "fewer", "below"), (lo, "consistent", "within")]:
            with self.subTest(verdict=verdict):
                f = build_site.visa_facts(visa_changed((path + ["observed"], observed), (path + ["verdict"], verdict)))
                self.assertTrue(f["visa_exch_en"].startswith(en), f["visa_exch_en"])
                self.assertNotEqual(f["visa_exch_ar"], "")
        with self.assertRaisesRegex(SystemExit, "verdict"):  # a verdict that disagrees with its own range
            build_site.visa_facts(visa_changed((path + ["observed"], hi + 1)))

    def test_wording_that_no_longer_matches_the_record_stops_the_build(self):
        engine, native = "dinov2-engine - descriptor", "dinov2-native - dinov2-engine"
        cases = {  # each change must trip its own guard
            "a gate that did not pass": (visa_changed((["gate", "passed"], False)), "gate passed"),
            "a repeat run that was not identical": (visa_changed((["repeat_run", "identical"], False)), "repeat run was identical"),
            "a repeat run in the same process": (visa_changed((["repeat_run", "process"], "the same process (injected scorers)")), "second process"),
            "stand-in scorers": (visa_changed((["identity", "dinov2", "note"], "injected scorers")), "second process"),
            "a quick run": (visa_changed((["quick"], True)), "full run"),
            "another protocol": (visa_changed((["protocol_digest"], "0" * 64)), "sealed"),
            "no seal commit": (visa_changed((["seal_commit"], None)), "sealed"),
            "a scorer scored on other images": (visa_changed((["pooled", "dinov2-engine", "full", "false_alert", "n"], 961)), "same official test images"),
            "most defects caught": (visa_changed((["pooled", "dinov2-native", "full", "detection", "rate"], 0.5)), "most defects pass"),
            "a detection step no longer resolved": (visa_changed(
                (["paired", engine, "detection", "ci95"], [-0.01, 0.12]), (["paired", engine, "detection", "reading"], "not resolved")), "catches more"),
            "an AUROC step no longer resolved": (visa_changed(
                (["paired", native, "auroc", "ci95"], [-0.001, 0.04]), (["paired", native, "auroc", "reading"], "not resolved")), "catches more"),
            "a reading that disagrees with its interval": (visa_changed((["paired", native, "detection", "ci95"], [-0.001, 0.04])), "reads 'higher'"),
            "a false-alert step now resolved": (visa_changed(
                (["paired", engine, "false_alert", "ci95"], [0.001, 0.02]), (["paired", engine, "false_alert", "reading"], "higher")), "false-alert differences"),
            "a comparison at another setting": (visa_changed((["paired", engine, "at"], "as_shipped, marginal")), "full configuration"),
            "an unknown verdict": (visa_changed((["exchangeability", "descriptor", "full", "pooled", "verdict"], "odd")), "verdict"),
        }
        for name, (record, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(SystemExit, message):
                build_site.visa_facts(record)

    def test_landing_shows_the_real_image_block_in_both_languages(self):
        page, _ = build_site.landing_html(self.packet, self.figures)
        block = re.search(r'<div class="models" id="visa">(.*?)<!-- /visa -->', page, re.S)
        self.assertIsNotNone(block, "the Real images block is missing")
        keys = [k for k in self.figures if k.startswith("visa_") and not k.startswith("visa_exch_")]
        for lang, heading in [("en", "Real images: BASIRA on VisA"), ("ar", "صور حقيقية: بصيرة على VisA")]:
            spans = "".join(re.findall(rf'<span data-l="{lang}">(.*?)</span>', block.group(1), re.S))
            with self.subTest(lang=lang):
                self.assertIn(heading, spans)
                for key in keys + [f"visa_exch_{lang}"]:
                    self.assertIn(self.figures[key], block.group(1) if key.endswith(("_detect", "_auroc_desc", "_auroc_native", "_fa")) else spans, key)
                self.assertIn("Zou et al., 2022", spans)
                self.assertIn("CC BY 4.0", spans)
                # The pooled false-alert interval is labelled as describing these fitted thresholds only (the spec).
                self.assertIn({"en": "fitted thresholds only", "ar": "العتبات المعايَرة هذه فقط"}[lang], spans)
                self.assertNotRegex(spans, r"promise held|وعد الإنذار الكاذب صمد")
        self.assertIn('href="report/visa/"', block.group(1))
        for path in ("report/visa/index.html", "report/visa/classes.html"):
            self.assertIn(path, build_site.COPIED, "the site serves the results pages where the link points")
        # The page no longer says that all its data is generated.
        self.assertNotRegex(page, r"All data is generated\.|كل البيانات مولّدة،? ولا شيء")
        limits = re.search(r'<section class="band" id="limits">(.*?)</section>', page, re.S).group(1)
        self.assertIn("VisA", limits)


OPSD = json.loads((ROOT / "reports/opsd/rasid.json").read_bytes())
FORBIDDEN = ("euro", "EUR", "€", "saving", "money")


def opsd_changed(*edits):
    """The OPSD record with each (path, value) edit applied."""
    record = copy.deepcopy(OPSD)
    for path, value in edits:
        node = record
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return record


class RealLoadFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = strict_load((ROOT / "reports/demo/demo_results.json").read_bytes())
        cls.figures = build_site.facts(cls.packet)

    def test_facts_read_the_real_load_evaluation(self):
        f = build_site.opsd_facts(OPSD)
        self.assertEqual({k: v for k, v in self.figures.items() if k.startswith("opsd_")}, f)
        s, p = OPSD["sites"], OPSD["pooled"]
        share = {a["a"] if "a" in a else None: a for a in p["accuracy"]}
        computed = {
            "opsd_sites": str(len(s)),
            "opsd_days": build_site.group(sum(x["counts"]["target_days"] for x in s.values())),
            "opsd_days_wh": build_site.group(s["industrial1"]["counts"]["target_days"]),
            "opsd_days_rb": build_site.group(s["industrial3"]["counts"]["target_days"]),
            "opsd_err_chronos": build_site.percent(np.mean([x["forecast"]["chronos_mae_share_of_load"] for x in s.values()])),
            "opsd_err_ridge": build_site.percent(np.mean([x["forecast"]["ridge_mae_share_of_load"] for x in s.values()])),
            "opsd_share_best": build_site.percent(p["share"]["chronos_risk_neutral"]["mean"]),
            "opsd_share_cand": build_site.percent(p["share"]["candidate"]["mean"]),
            "opsd_share_naive": build_site.percent(p["share"]["seasonal_naive"]["mean"]),
            "opsd_verified": build_site.group(sum(pl["verified_feasible"] for x in s.values() for pl in x["plans"].values())),
            "opsd_plans": build_site.group(sum(pl["kept_days"] + pl["rejected"] + pl["no_plan"] for x in s.values() for pl in x["plans"].values())),
        }
        self.assertEqual({k: f[k] for k in computed}, computed)
        gap = next(a for a in p["accuracy"] if (a["a"], a["b"]) == ("chronos_mae", "ridge_mae"))
        self.assertEqual((f["opsd_err_gap"], f["opsd_err_gap_lo"], f["opsd_err_gap_hi"]),
                         tuple(build_site.signed(v * 100, 1) for v in (gap["mean"], *gap["ci95"])))
        cand = next(c for c in p["paired"] if (c["a"], c["b"]) == ("candidate", "seasonal_naive"))
        self.assertEqual(f["opsd_cand_naive"], build_site.signed(cand["mean"] * 100, 1) + "%")
        self.assertEqual(len(share), 2)

    def test_phrases_follow_the_interval(self):
        path = ["pooled", "paired", 0]
        for (lo, hi), en in [((0.01, 0.02), "costs more than"), ((-0.02, -0.01), "costs less than"), ((-0.01, 0.01), "cannot be told apart from")]:
            with self.subTest(interval=(lo, hi)):
                reading = "higher" if lo > 0 else "lower" if hi < 0 else "not resolved"
                f = build_site.opsd_facts(opsd_changed((path + ["ci95"], [lo, hi]), (path + ["reading"], reading),
                                                       (path + ["mean"], (lo + hi) / 2)))
                self.assertEqual(f["opsd_cand_naive_en"], en)
                self.assertNotEqual(f["opsd_cand_naive_ar"], "")

    def test_wording_that_no_longer_matches_the_record_stops_the_build(self):
        cases = {
            "a gate that did not pass": (opsd_changed((["gate", "passed"], False)), "gate passed"),
            "a repeat that was not identical": (opsd_changed((["repeat_run", "identical"], False)), "repeat"),
            "a repeat in the same process": (opsd_changed((["repeat_run", "process"], "the same process (injected forecaster)")), "second process"),
            "an injected forecaster": (opsd_changed((["identity", "chronos"], {"note": "injected forecaster"})), "second process"),
            "a quick run": (opsd_changed((["quick"], {"days_per_site": 10, "draws": 50})), "full run"),
            "another protocol": (opsd_changed((["protocol_digest"], "0" * 64)), "sealed"),
            "no seal commit": (opsd_changed((["seal_commit"], None)), "sealed"),
            "a day count that disagrees with the rows": (opsd_changed((["sites", "industrial1", "counts", "target_days"], 1)), "days"),
            "another set of sites": (opsd_changed((["sites"], {"industrial1": OPSD["sites"]["industrial1"]})), "two business sites"),
            "a reading that disagrees with its interval": (opsd_changed((["pooled", "paired", 0, "reading"], "lower")), "reads"),
            "an accuracy reading that disagrees with its interval": (opsd_changed((["pooled", "accuracy", 1, "reading"], "higher")), "reads"),
        }
        for name, (record, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(SystemExit, message):
                build_site.opsd_facts(record)

    def test_landing_shows_the_real_load_block_in_both_languages(self):
        page, _ = build_site.landing_html(self.packet, self.figures)
        block = re.search(r'<div class="models" id="opsd">(.*?)<!-- /opsd -->', page, re.S)
        self.assertIsNotNone(block, "the Real load block is missing")
        keys = [k for k in self.figures if k.startswith("opsd_") and not k.endswith(("_en", "_ar"))]
        for lang, heading in [("en", "Real load: RASID on two business sites"), ("ar", "أحمال حقيقية: راصد في موقعين تجاريين")]:
            spans = "".join(re.findall(rf'<span data-l="{lang}">(.*?)</span>', block.group(1), re.S))
            with self.subTest(lang=lang):
                self.assertIn(heading, spans)
                for key in keys + [f"opsd_err_phrase_{lang}", f"opsd_ridge_naive_{lang}", f"opsd_cand_naive_{lang}"]:
                    self.assertIn(self.figures[key], block.group(1), key)
                self.assertIn("CC BY 4.0", spans)
        text = block.group(1).replace("Europe", "")
        for word in FORBIDDEN:
            self.assertNotIn(word, text)
        self.assertIn('href="report/opsd/"', block.group(1))
        self.assertIn("report/opsd/index.html", build_site.COPIED)
        limits = re.search(r'<section class="band" id="limits">(.*?)</section>', page, re.S).group(1)
        self.assertIn("OPSD", limits)


if __name__ == "__main__":
    unittest.main()
