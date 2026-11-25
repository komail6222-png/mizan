"""The introduction page (scripts/build_site.py): every figure is read from reports/, and the wording it fills is guarded."""
import copy
import json
import re
import unittest
from pathlib import Path

from mizan.core.contract import strict_load
from scripts import build_site

ROOT = Path(__file__).resolve().parents[1]
HF = {k: json.loads((ROOT / f"reports/hf/{k}.json").read_bytes()) for k in ("basira", "rasid", "sanad")}


def hf_facts(basira=None, rasid=None, sanad=None):
    return build_site.hf_facts(basira or HF["basira"], rasid or HF["rasid"], sanad or HF["sanad"])


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
            "Chronos error no longer lower": (change("rasid", ["model", "forecast", "chronos_mae_kw"], 2.9), "lower load error"),
            "Chronos pinball no longer worse": (change("rasid", ["model", "forecast", "chronos_mean_pinball"], 0.7), "score worse"),
            "risk-weighted Chronos plan no longer resolved costlier": (change("rasid", ["model", "paired_comparisons", 2, "mean_daily_difference_95ci"], [-0.1, 1.6]), "risk-weighted"),
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
                            "hf_rasid_weighted", "hf_rasid_weighted_lo", "hf_rasid_weighted_hi"):
                    self.assertIn(self.figures[key], spans, key)
        self.assertIn('href="report/#hf-section"', block.group(1))
        report = (ROOT / "reports/evaluation/index.html").read_text(encoding="utf-8")
        self.assertIn('<section id="hf-section"', report, "the link's target exists in the report page")

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


if __name__ == "__main__":
    unittest.main()
