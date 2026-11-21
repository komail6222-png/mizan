"""BASIRA 2.3: exact training-conditional guarantees and the optional PAC decision rule."""
from fractions import Fraction
import unittest

from mizan import evaluation, examples
from mizan.basira import engine as vision
from mizan.core.contract import Refused

ALPHA = Fraction(1, 20)


class ExactGuaranteeTests(unittest.TestCase):
    def test_exceedance_is_the_exact_binomial_tail(self):
        self.assertEqual(vision.exceedance(24, 24, ALPHA), Fraction(19, 20) ** 24)
        self.assertEqual(vision.exceedance(3, 2, ALPHA), 3 * Fraction(19, 20) ** 2 * ALPHA + Fraction(19, 20) ** 3)
        for m in (5, 24, 60):
            values = [vision.exceedance(m, k, ALPHA) for k in range(1, m + 1)]
            self.assertTrue(all(a >= b for a, b in zip(values, values[1:])), "exceedance must fall as the rank rises")

    def test_pac_rank_and_minimum_calibration_size(self):
        delta = Fraction(1, 10)
        self.assertEqual(vision.minimum_m(ALPHA, delta), 45)          # 0.95^45 <= 0.1 < 0.95^44
        self.assertEqual(vision.minimum_m(Fraction(1, 10), delta), 22)
        self.assertIsNone(vision.pac_rank(44, ALPHA, delta))
        self.assertEqual(vision.pac_rank(45, ALPHA, delta), 45)
        k = vision.pac_rank(100, ALPHA, delta)
        self.assertLessEqual(vision.exceedance(100, k, ALPHA), delta)
        self.assertGreater(vision.exceedance(100, k - 1, ALPHA), delta)

    def test_marginal_decisions_are_unchanged_and_report_their_exceedance(self):
        r = vision.rank_decision([0] * 24, 1, ".05")
        self.assertEqual((r["decision"], r["k"], r["guarantee"]), ("FLAG", 24, "marginal"))
        self.assertAlmostEqual(r["training_conditional"]["marginal_rank_exceedance"], float(Fraction(19, 20) ** 24))
        self.assertEqual(r["training_conditional"]["minimum_m_for_pac"], 45)
        self.assertIsNone(vision.rank_decision([0] * 4, 1, ".05")["training_conditional"]["marginal_rank"])

    def test_pac_rule_flags_strictly_and_abstains_when_m_is_too_small(self):
        scores = list(range(50))
        pac = {"type": "pac", "delta": "0.1"}
        self.assertEqual(vision.rank_decision(scores, 49.5, ".05", pac)["decision"], "FLAG")
        tie = vision.rank_decision(scores, 49, ".05", pac)
        self.assertEqual((tie["decision"], tie["mode"]), ("REVIEW", "NORMAL_ALERTS_ONLY_PAC"))
        small = vision.rank_decision(list(range(24)), 1e9, ".05", pac)
        self.assertEqual((small["decision"], small["mode"]), ("REVIEW", "ADVISORY_INSUFFICIENT_NORMALS_FOR_PAC"))
        for bad in [{"type": "pac", "delta": "1"}, {"type": "pac", "delta": "0"}, {"type": "pac", "delta": "x"},
                    {"type": "other"}, "pac", {"type": "pac", "delta": "0.1", "extra": 1}]:
            with self.subTest(spec=bad), self.assertRaises(Refused):
                vision.rank_decision(scores, 1, ".05", bad)


class EngineIdentityTests(unittest.TestCase):
    def test_bank_identity_is_unchanged_from_release_2_1(self):
        p = examples.inspection()
        _, _, identity, _, _ = vision.fit(p["reference"], p["calibration"])
        self.assertEqual(identity, "4ffef5b14d108684a523ae3d44c07bb9685f756cbd3c3a6043097ed1abc4de2e")

    def test_pac_request_end_to_end_and_old_engine_refused(self):
        p = examples.inspection(cal_count=50)
        p["guarantee"] = {"type": "pac", "delta": "0.1"}
        r = vision.evaluate(p, examples.profile("basira"))
        self.assertEqual((r["mode"], r["k"], r["descriptor"]), ("NORMAL_ALERTS_ONLY_PAC", 50, vision.DESCRIPTOR))
        with self.assertRaises(Refused) as caught:
            vision.evaluate(p, {"engine": vision.DESCRIPTOR})
        self.assertEqual(caught.exception.code, "PROFILE_UPGRADE_REQUIRED")


class CalibrationStudyTests(unittest.TestCase):
    def test_small_study_runs_and_matches_theory_fields(self):
        r = evaluation.calibration_study(draws=20, sizes=(24, 50), pools=(150, 300, 20))
        self.assertEqual(r["minimum_m_for_pac"], 45)
        by_m = {row["m"]: row for row in r["rows"]}
        self.assertIsNone(by_m[24]["pac_rank"])
        self.assertEqual(by_m[50]["pac_rank"], 50)
        self.assertAlmostEqual(by_m[24]["marginal_theoretical_share_above_alpha"], float(Fraction(19, 20) ** 24))
        self.assertTrue(0 <= r["shipped_calibration"]["probability_of_a_bank_at_least_this_bad"] <= 1)


if __name__ == "__main__":
    unittest.main()
