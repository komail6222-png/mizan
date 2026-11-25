import json
import unittest
from fractions import Fraction

import numpy as np
from scipy import stats

from mizan.basira import engine as b


class StatisticsTests(unittest.TestCase):
    def test_clopper_pearson_edges(self):
        from mizan.visa_evaluation import clopper_pearson
        lo, hi = clopper_pearson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 1 - 0.025 ** (1 / 10), places=12)
        lo, hi = clopper_pearson(5, 5)
        self.assertAlmostEqual(lo, 0.025 ** (1 / 5), places=12)
        self.assertEqual(hi, 1.0)
        lo, hi = clopper_pearson(3, 20)
        self.assertLess(lo, 3 / 20)
        self.assertGreater(hi, 3 / 20)

    def test_rule_rank(self):
        from mizan.visa_evaluation import rule_rank
        self.assertEqual(rule_rank(24, "marginal"), 24)
        self.assertEqual(rule_rank(100, "marginal"), 96)
        self.assertEqual(rule_rank(100, "pac"), b.pac_rank(100, Fraction(1, 20), Fraction(1, 10)))

    def test_exchangeability_one_class_equals_beta_binomial(self):
        from mizan.visa_evaluation import exchangeability
        n, m, k = 100, 100, 96
        dist = stats.betabinom(n, m - k + 1, k)
        r = exchangeability([(n, 7)], m, k)
        self.assertEqual(r["observed"], 7)
        self.assertAlmostEqual(r["expected_mean"], n * (m - k + 1) / (m + 1), places=12)
        self.assertEqual(r["range95"], [int(dist.ppf(.025)), int(dist.ppf(.975))])
        self.assertAlmostEqual(r["p_two_sided"], min(1.0, 2 * min(dist.cdf(7), dist.sf(6))), places=12)
        self.assertEqual(r["verdict"], "consistent")
        self.assertEqual(exchangeability([(n, 40)], m, k)["verdict"], "more")

    def test_exchangeability_convolution_is_a_distribution(self):
        from mizan.visa_evaluation import exchangeability
        r = exchangeability([(100, 4), (50, 2)], 24, 24)
        self.assertAlmostEqual(sum(r["pmf"]), 1.0, places=12)
        self.assertEqual(len(r["pmf"]), 151)
        self.assertEqual(r["observed"], 6)

    def test_auroc_counts_ties_as_half(self):
        from mizan.visa_evaluation import auroc
        self.assertEqual(auroc([0, 1], [1, 2]), 0.875)
        self.assertEqual(auroc([0, 1, 2], [5, 6]), 1.0)
        self.assertEqual(auroc([5, 6], [0, 1, 2]), 0.0)

    def test_auroc_interval_brackets_the_estimate(self):
        from mizan.visa_evaluation import auroc, auroc_interval
        rng = np.random.default_rng(1)
        normal, anomaly = rng.normal(0, 1, 60), rng.normal(1, 1, 40)
        lo, hi = auroc_interval(normal, anomaly)
        self.assertLessEqual(lo, auroc(normal, anomaly))
        self.assertGreaterEqual(hi, auroc(normal, anomaly))
        self.assertEqual((lo, hi), auroc_interval(normal, anomaly))   # fixed seed

    def test_paired_difference_readings(self):
        from mizan.visa_evaluation import paired_difference
        pooled = lambda strata: float(np.mean(np.concatenate([s["a"] for s in strata]))
                                      - np.mean(np.concatenate([s["b"] for s in strata])))
        same = [{"a": np.array([1, 0, 1, 1]), "b": np.array([1, 0, 1, 1])}, {"a": np.array([0, 1]), "b": np.array([0, 1])}]
        r = paired_difference(same, pooled)
        self.assertEqual(r["difference"], 0.0)
        self.assertEqual(r["reading"], "not resolved")
        shifted = [{"a": s["b"] + 1, "b": s["b"]} for s in same]
        r = paired_difference(shifted, pooled)
        self.assertAlmostEqual(r["difference"], 1.0, places=12)
        self.assertAlmostEqual(r["ci95"][0], 1.0, places=12)
        self.assertAlmostEqual(r["ci95"][1], 1.0, places=12)
        self.assertEqual(r["reading"], "higher")
        self.assertEqual(paired_difference([{"a": s["b"], "b": s["a"]} for s in shifted], pooled)["reading"], "lower")


def _small_features(image):
    """A stand-in feature grid (12 x 12 x 1) so harness tests run in milliseconds."""
    return np.asarray(image.convert("L").resize((12, 12)), dtype=np.float64)[..., None] / 255


class HarnessTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from mizan.visa import build_manifest, protocol_split, read_split
        from tests.visa_fixture import make_visa_tree
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.csv = make_visa_tree(self.root)
        self.manifest = build_manifest(self.root, read_split(self.csv), "a" * 64, "b" * 64)
        self.split = protocol_split(self.root, self.manifest, "candle", sizes=(2, 2))

    def tearDown(self):
        self.tmp.cleanup()

    def line(self, scorer, refuse=None):
        from mizan.visa_evaluation import product_line
        s = self.split
        configs = {"small": {"reference": s["reference"][:1] + s["reference"][1:], "calibration": s["calibration"],
                             "rules": ["marginal"]}}
        tests = [(p, "normal") for p in s["test_normal"]] + [(p, "anomaly") for p in s["test_anomaly"]]
        return product_line(self.root, configs, tests, scorer, refuse=refuse or {})

    def test_fixture_gate_passes_with_shipped_descriptor(self):
        from mizan.visa_evaluation import fixture_gate
        gate = fixture_gate()
        self.assertTrue(gate["passed"], gate["mismatches"][:3])
        self.assertEqual(gate["mismatches"], [])
        self.assertEqual(sum(name.startswith("row[") for name in gate["checked"]), 80 * 4)
        self.assertIn("bank_digest", gate["checked"])

    def test_fixture_gate_fails_when_one_p_value_differs(self):
        from unittest import mock
        from mizan.visa_evaluation import fixture_gate
        original, calls = b.rank_decision, []
        def altered(*args, **kwargs):
            out = original(*args, **kwargs)
            calls.append(1)
            if len(calls) == 5:
                out = {**out, "p_value": out["p_value"] + 1e-6}
            return out
        with mock.patch.object(b, "rank_decision", altered):
            gate = fixture_gate()
        self.assertFalse(gate["passed"])
        self.assertTrue(any("row[4]" in m["name"] and "p_value" in m["name"] for m in gate["mismatches"]))

    def test_dinov2_gate_tolerance(self):
        from mizan.visa_evaluation import dinov2_gate
        shipped = {"calibration_m": 24, "bank_digest": "x", "detected": 1, "false_alerts": 0,
                   "rows": [{"seed": 1, "condition": "intact", "score": 1.0, "p_value": .5, "flag": False},
                            {"seed": 2, "condition": "crack", "score": 2.0, "p_value": .04, "flag": True}]}
        same = dinov2_gate(shipped, shipped)
        self.assertTrue(same["passed"] and same["bit_exact"])
        near = {**shipped, "bank_digest": "y", "rows": [{**shipped["rows"][0], "score": 1.0 * (1 + 1e-9)}, shipped["rows"][1]]}
        close = dinov2_gate(near, shipped)
        self.assertTrue(close["passed"])
        self.assertFalse(close["bit_exact"])
        flipped = {**shipped, "rows": [shipped["rows"][0], {**shipped["rows"][1], "flag": False}]}
        self.assertFalse(dinov2_gate(flipped, shipped)["passed"])

    def test_native_scorer_never_uses_engine_decode(self):
        from unittest import mock
        from mizan.visa_evaluation import Scorer
        with mock.patch.object(b, "decode_image", side_effect=AssertionError("engine decode on the native path")) as spy:
            line = self.line(Scorer("native-stub", _small_features, False, "test/native"))
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(len(line["rows"]), 3)

    def test_query_leak_is_refused_not_scored(self):
        from mizan.visa_evaluation import Scorer, leaks
        leak = self.root / self.split["test_normal"][0]
        leak.write_bytes((self.root / self.split["reference"][0]).read_bytes())
        refuse = leaks(self.root, self.split)
        self.assertEqual(refuse, {self.split["test_normal"][0]: "QUERY_LEAK"})
        line = self.line(Scorer("stub", _small_features, True, "test/engine"), refuse)
        row = next(r for r in line["rows"] if r["path"] == self.split["test_normal"][0])
        self.assertEqual(row["refused"], "QUERY_LEAK")
        self.assertIsNone(row["scores"]["small"])
        self.assertEqual(row["flags"]["small"], {})

    def test_features_computed_once_per_image(self):
        from mizan.visa_evaluation import Scorer, product_line
        calls = []
        def spy(image):
            calls.append(1)
            return _small_features(image)
        s = self.split
        configs = {"one": {"reference": s["reference"][:2], "calibration": s["calibration"][:1], "rules": ["marginal"]},
                   "two": {"reference": s["reference"], "calibration": s["calibration"], "rules": ["marginal", "pac"]}}
        tests = [(p, "normal") for p in s["test_normal"]] + [(p, "anomaly") for p in s["test_anomaly"]]
        line = product_line(self.root, configs, tests, Scorer("stub", spy, True, "test/engine"))
        fitted = sum(len(c["reference"]) + len(c["calibration"]) for c in configs.values())
        self.assertEqual(len(calls), fitted + len(tests))
        self.assertEqual(set(line["rows"][0]["scores"]), {"one", "two"})
        self.assertEqual(set(line["rows"][0]["flags"]["two"]), {"marginal", "pac"})


class RecordTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from mizan.visa import build_manifest, read_split
        from tests.visa_fixture import make_visa_tree
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.manifest = build_manifest(self.root, read_split(make_visa_tree(self.root)), "a" * 64, "b" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def test_study_caps_m_by_pool_and_notes_it(self):
        from mizan.visa_evaluation import study
        rng = np.random.default_rng(3)
        r = study(rng.normal(size=30), rng.normal(size=40), rng.normal(2, 1, size=20), draws=10, sizes=(24, 50))
        self.assertEqual([row["m"] for row in r["rows"]], [24])
        self.assertEqual(r["capped"], [{"m": 50, "pool": 30}])
        row = r["rows"][0]
        self.assertEqual(row["draws"], 10)
        self.assertAlmostEqual(row["theoretical_share_above_alpha"], float(b.exceedance(24, 24, Fraction(1, 20))), places=12)
        self.assertTrue(0 <= row["mean_false_alert"] <= 1 and 0 <= row["mean_detection"] <= 1)

    def test_examples_follow_the_rule(self):
        from mizan.visa_evaluation import pick_examples
        rows = {"pcb1": [{"path": f"p{i}", "label": "anomaly", "score": s, "flag": s > 5} for i, s in enumerate([9, 7, 6, 1, 2])],
                "candle": [{"path": f"c{i}", "label": "normal", "score": s, "flag": False} for i, s in enumerate([3, 1, 2, 4])]}
        picked = pick_examples(rows)
        self.assertEqual(picked["caught"], {"class": "pcb1", "path": "p1", "score": 7})      # flagged 6, 7, 9: lower median 7
        self.assertEqual(picked["missed"], {"class": "pcb1", "path": "p3", "score": 1})      # missed 1, 2: lower median 1
        self.assertEqual(picked["passed"], {"class": "candle", "path": "c2", "score": 2})    # passed 1, 2, 3, 4: lower median 2
        self.assertIsNone(picked["false_alert"])

    def stub_scorers(self):
        from mizan.visa_evaluation import Scorer
        return [Scorer("descriptor", _small_features, True, "test/descriptor"),
                Scorer("dinov2-engine", lambda im: _small_features(im) ** 2, True, "test/engine"),
                Scorer("dinov2-native", lambda im: _small_features(im) + .01, False, "test/native")]

    def test_record_shape_and_no_test_leak(self):
        from mizan.visa import protocol_split
        from mizan.visa_evaluation import evaluate_visa
        r = evaluate_visa(self.root, self.manifest, self.stub_scorers(), quick=True)
        for key in ("identity", "settings", "notes", "splits", "per_class", "pooled", "exchangeability", "auroc",
                    "paired", "study", "examples", "rows"):
            self.assertIn(key, r)
        tests = [x["path"] for x in self.manifest["images"] if x["split"] == "test"]
        self.assertEqual(sorted(row["path"] for row in r["rows"]), sorted(tests))
        for cls in ("candle", "pcb1"):
            s = protocol_split(self.root, self.manifest, cls, sizes=(2, 2))
            fitted = set(s["reference"] + s["calibration"] + s["draw_pool"] + s["eval_pool"])
            self.assertFalse(fitted & set(tests))
        self.assertEqual(set(r["per_class"]), {"descriptor", "dinov2-engine", "dinov2-native"})
        self.assertEqual(set(r["per_class"]["descriptor"]), {"as_shipped", "full", "full_pac"})
        self.assertEqual({p["comparison"] for p in r["paired"]},
                         {"dinov2-engine - descriptor", "dinov2-native - dinov2-engine"})
        self.assertEqual(set(r["examples"]), {"caught", "missed", "false_alert", "passed"})
        self.assertNotIn("pmf", json.dumps(r["exchangeability"]))

    def test_pooled_rates_equal_sums_of_classes(self):
        from mizan.visa_evaluation import evaluate_visa
        r = evaluate_visa(self.root, self.manifest, self.stub_scorers(), quick=True)
        for scorer, by_rule in r["per_class"].items():
            for rule, by_class in by_rule.items():
                for measure in ("false_alert", "detection"):
                    pooled = r["pooled"][scorer][rule][measure]
                    self.assertEqual(pooled["x"], sum(c[measure]["x"] for c in by_class.values()))
                    self.assertEqual(pooled["n"], sum(c[measure]["n"] for c in by_class.values()))


if __name__ == "__main__":
    unittest.main()
