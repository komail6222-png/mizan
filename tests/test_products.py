import copy
import unittest
from pathlib import Path
from datetime import timedelta

import numpy as np
from mizan import examples
from mizan.basira import engine as vision
from mizan.core.contract import digest, Refused
from mizan.rasid import engine as energy
from mizan.rasid.forecast import local_day, records, quantile_scores
from mizan.rasid.verify import tail_cost, verify
from mizan.sanad import engine as procurement
from mizan.service import verify_output


class ProcurementTests(unittest.TestCase):
    def test_policy_reuses_parse_and_preserves_original_spans(self):
        p = examples.procurement()
        result = procurement.evaluate(p, examples.profile("sanad"))
        second = result["policy_comparison"]
        self.assertEqual(result["requirements"][0]["verdict"], "REVIEW")
        self.assertEqual(second["requirements"][0]["verdict"], "MEETS")
        self.assertEqual(result["extraction_digest"], second["extraction_digest"])
        self.assertNotEqual(result["evaluation_digest"], second["evaluation_digest"])
        pages = {procurement.source_id(d): d["pages"] for d in p["documents"]}
        for rule in result["requirements"]:
            for evidence in rule["evidence"]:
                page = pages[evidence["source"]][evidence["page"] - 1]
                self.assertEqual(page[evidence["start"]:evidence["end"]], evidence["quote"])

    def test_unapproved_bytes_cannot_support_verdict(self):
        p = examples.procurement()
        for doc in p["documents"]:
            doc["original_file_sha256"] = "a" * 64
        got = procurement.evaluate(p, examples.profile("sanad"))
        self.assertEqual(got["covered_sources"], 0)
        self.assertTrue(all(r["verdict"] == "REVIEW" for r in got["requirements"]))

    def test_unsupported_context_and_model_mismatch_abstain(self):
        for page in ["Model: Atlas-20\nRated apparent power: 20 kVA under a special configuration",
                     "Model: other-UPS\nRated apparent power: 20 kVA"]:
            p = examples.procurement()
            d = {"name": "synthetic", "pages": [page]}
            p["documents"] = [d]
            profile = {"engine": procurement.PROFILE, "approved_sources": {procurement.source_id(d): 1}}
            self.assertEqual(procurement.evaluate(p, profile)["requirements"][0]["verdict"], "REVIEW")

    def test_changed_verdict_and_incompatible_profile_refused(self):
        p, profile = examples.procurement(), examples.profile("sanad")
        result = procurement.evaluate(p, profile)
        result["requirements"][0]["verdict"] = "MEETS"
        with self.assertRaises(Refused):
            verify_output("sanad", p, profile, result)
        with self.assertRaises(Refused):
            procurement.evaluate(p, {**profile, "engine": "unknown"})


class VisionTests(unittest.TestCase):
    def test_finite_sample_boundaries_and_conservative_ties(self):
        for size, want in [(4, "REVIEW"), (18, "REVIEW"), (19, "FLAG")]:
            r = vision.rank_decision([0] * size, 1, ".05")
            self.assertEqual(r["p_denominator"], size + 1)
            self.assertEqual(r["decision"], want)
            self.assertFalse(r["automatic_pass"])
        ties = vision.rank_decision([1] * 19, 1, ".05")
        self.assertEqual(ties["p_value"], 1)
        with self.assertRaises(Refused):
            vision.rank_decision([float("nan")], 1, ".05")

    def test_duplicate_capture_group_and_pixels_refused(self):
        a, b, c = [examples.item(i) for i in range(3)]
        for duplicate in [{**c, "group": a["group"]}, {**a, "group": "second-view"}]:
            with self.assertRaises(Refused):
                vision.fit([a, b], [duplicate])

    def test_query_leak_and_changed_profile(self):
        p = examples.inspection(4)
        p["query"] = p["reference"][0]
        with self.assertRaises(Refused):
            vision.evaluate(p, examples.profile("basira"))
        with self.assertRaises(Refused):
            vision.evaluate(p, {"engine": "different-preprocessing"})

    def test_chunked_distance_invariance_and_overlay(self):
        p = examples.inspection(4)
        bank, _, _, _, _ = vision.fit(p["reference"], p["calibration"])
        im = vision.decode_image(p["query"]["png"])
        features = vision.features(im)
        a, heat = vision.score(features, bank, tile=1)
        b, other = vision.score(features, bank, tile=8)
        self.assertEqual(a, b)
        np.testing.assert_array_equal(heat, other)
        self.assertEqual(vision.decode_image(vision.overlay(im, heat)).size, im.size)


class EnergyTests(unittest.TestCase):
    def test_two_hour_hand_oracle(self):
        r = energy.evaluate(examples.peak_oracle(), examples.profile("rasid"))
        self.assertEqual(r["certificate"]["physical_status"], "VERIFIED_FEASIBLE")
        self.assertAlmostEqual(r["certificate"]["objective"], 2)
        np.testing.assert_allclose(r["plan"]["grid"][0], [2, 0], atol=1e-7)

    def test_fractional_tail_negative_costs_and_bad_beta(self):
        self.assertAlmostEqual(tail_cost([-3, 2, 10], [.65, .30, .05], .90), 6)
        self.assertAlmostEqual(tail_cost([-8, -2], [.9, .1], .75), -5.6)
        with self.assertRaises(Refused):
            tail_cost([3], [1], 1)
        p = examples.peak_oracle()
        p["problem"]["beta"] = 1
        with self.assertRaises(Refused):
            energy.materialize(p)

    def test_vintage_filter_and_dst_day_length(self):
        self.assertEqual(len(local_day("2026-03-29", "Europe/Berlin")), 23)
        self.assertEqual(len(local_day("2026-10-25", "Europe/Berlin")), 25)
        p = examples.energy()
        origin = local_day(p["day"], "UTC")[0]
        later = (origin + timedelta(hours=6)).isoformat()
        first = energy.materialize(p)
        p["history"] += [{**p["history"][0], "known_at": later, "load": -999},
                         {"time": later, "known_at": later, "load": "unknown", "pv": 1}]
        self.assertEqual(digest(first), digest(energy.materialize(p)))

    def test_irregular_quantile_quadrature_and_nan(self):
        r = quantile_scores([.1, .2, .8], [[0, 0, 0]], [2])
        self.assertAlmostEqual(r["truncated_crps"], .1 * (.2 + .4) + .6 * (.4 + 1.6))
        with self.assertRaises(Refused):
            quantile_scores([.1, .9], [[0, 1]], [float("nan")])

    def test_constraint_mutations_and_stale_gap(self):
        result = energy.evaluate(examples.energy(), examples.profile("rasid"))
        p, raw = result["problem"], result["plan"]
        for key, index, change in [
            ("grid", (0, 0), 2), ("up", (0, 0), 2), ("overload", (0, 0), 1),
            ("soc", (1,), 2), ("mode", (0,), .4), ("interruptible", (0, 0), 4),
            ("block_start", (0, 0), .3), ("peak", (0,), 2), ("curtail", (0, 0), 5),
            ("tail_excess", (0,), 100), ("scenario_cost", (0,), 2)
        ]:
            with self.subTest(key=key):
                candidate = copy.deepcopy(raw)
                value = candidate[key]
                for i in index[:-1]:
                    value = value[i]
                value[index[-1]] += change
                with self.assertRaises(Refused):
                    verify(p, candidate)
        mutation = copy.deepcopy(raw)
        mutation["grid"][0][0] = float("nan")
        with self.assertRaises(Refused):
            verify(p, mutation)
        mutation = copy.deepcopy(raw)
        mutation["solver"]["lower_bound"] = result["certificate"]["objective"] + 10
        with self.assertRaises(Refused):
            verify(p, mutation)
        mutation = copy.deepcopy(raw)
        del mutation["soc"]
        with self.assertRaises(Refused):
            verify(p, mutation)
        strict_cycles = copy.deepcopy(p)
        strict_cycles["assets"]["cycles"] = 0
        self.assertGreater(sum(raw["discharge"]), .1)
        with self.assertRaisesRegex(Refused, "C6_cycles"):
            verify(strict_cycles, raw)

    def test_pure_tail_and_relaxed_plan_classification(self):
        packet = examples.peak_oracle()
        p = packet["problem"]
        p["risk"] = 1
        p["assets"].update(capacity=0, power=0, initial=0, terminal=0, limit=0)
        p["scenarios"].append([[2, 0], [3, 0]])
        p["weights"] = [.8, .2]
        result = energy.evaluate(packet, examples.profile("rasid"))
        c = result["certificate"]
        self.assertEqual(c["physical_status"], "VERIFIED_RELAXED_DIAGNOSTIC")
        self.assertAlmostEqual(c["cvar"], c["objective"])
