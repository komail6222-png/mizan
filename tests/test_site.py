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
            "hf_sanad_lexical_r1": "96.9%", "hf_sanad_e5_r1": "80.5%",
            "hf_sanad_lexical_r1_en": "61.1%", "hf_sanad_e5_r1_en": "77.7%",
            "hf_sanad_lexical_r1_ar": "60.2%", "hf_sanad_e5_r1_ar": "72.5%",
        }
        self.assertEqual({k: self.figures.get(k) for k in expected}, expected)
        self.assertEqual(hf_facts(), {k: v for k, v in self.figures.items() if k.startswith("hf_")})

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
            "a gate that did not pass": (change("rasid", ["gate", "passed"], False), "rasid.json: .*every gate passed"),
            "a repeat run that was not identical": (change("sanad", ["repeat_run", "identical"], False), "sanad.json: .*repeated identically"),
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
                for key in ("hf_rasid_value", "hf_rasid_lo", "hf_rasid_hi", f"hf_rasid_phrase_{lang}"):
                    self.assertIn(self.figures[key], spans, key)
        self.assertIn('href="report/#hf-section"', block.group(1))
        report = (ROOT / "reports/evaluation/index.html").read_text(encoding="utf-8")
        self.assertIn('<section id="hf-section"', report, "the link's target exists in the report page")


if __name__ == "__main__":
    unittest.main()
