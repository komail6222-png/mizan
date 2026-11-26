"""RASID on real load (OPSD): within-run statistics, the day loop, the gates, the record and the runner."""
import unittest

import numpy as np

from mizan.core.contract import Refused


class StatisticsTests(unittest.TestCase):
    def test_blocks_never_span_a_gap(self):
        from mizan.opsd_evaluation import run_blocks
        blocks = run_blocks([10, 3, 8])
        bounds = [(0, 10), (10, 13), (13, 21)]
        for start, length in blocks:
            self.assertTrue(any(a <= start and start + length <= b for a, b in bounds), (start, length))
        self.assertEqual([b for b in blocks if 10 <= b[0] < 13], [(10, 3)])
        self.assertEqual(sorted(s for s, n in blocks if s < 10), [0, 1, 2, 3])
        self.assertTrue(all(n == 7 for s, n in blocks if s < 10 or s >= 13))

    def test_short_runs_are_whole_blocks(self):
        from mizan.opsd_evaluation import block_draws
        draws = block_draws(4, [2, 2], draws=200, seed=1)
        for row in draws:
            for k in (0, 2):
                self.assertIn(tuple(row[k:k + 2]), {(0, 1), (2, 3)})

    def test_paired_reading_follows_the_interval(self):
        from mizan.opsd_evaluation import paired
        runs = [20]
        self.assertEqual(paired(np.full(20, 2.0), runs, draws=200)["reading"], "higher")
        self.assertEqual(paired(np.full(20, -2.0), runs, draws=200)["reading"], "lower")
        self.assertEqual(paired(np.array([1.0, -1.0] * 10), runs, draws=200)["reading"], "not resolved")
        touching = paired(np.array([0.0] * 30 + [-1.0]), [31], draws=400)
        self.assertEqual(touching["ci95"][1], 0.0)          # the upper end is exactly zero
        self.assertEqual(touching["reading"], "not resolved")
        result = paired(np.array([1.0, 2.0, 3.0]), [3], draws=50)
        self.assertEqual((result["mean"], result["total"]), (2.0, 6.0))

    def test_share_of_value_matches_hand_arithmetic(self):
        from mizan.opsd_evaluation import share_interval, share_of_value
        rows = [{"no_battery_cost": 10, "perfect_foresight_cost": 6, "candidate_cost": 7},
                {"no_battery_cost": 10, "perfect_foresight_cost": 6, "candidate_cost": 8}]
        self.assertEqual(share_of_value(rows, "candidate"), 0.625)
        self.assertIsNone(share_of_value([{"no_battery_cost": 5, "perfect_foresight_cost": 5, "candidate_cost": 5}], "candidate"))
        interval = share_interval(rows, "candidate", [2], draws=100)
        self.assertEqual(interval["value"], 0.625)
        self.assertTrue(interval["ci95"][0] <= 0.625 <= interval["ci95"][1])

    def test_pooled_weights_sites_equally(self):
        from mizan.opsd_evaluation import pooled
        result = pooled({"a": (np.full(20, 0.1), [20]), "b": (np.full(200, 0.3), [200])}, draws=100)
        self.assertAlmostEqual(result["mean"], 0.2, places=12)
        self.assertAlmostEqual(result["ci95"][0], 0.2, places=12)
        self.assertEqual(result["reading"], "higher")

    def test_draws_are_reproducible(self):
        from mizan.opsd_evaluation import block_draws
        a = block_draws(30, [12, 18], draws=50, seed=9)
        self.assertEqual(a.shape, (50, 30))
        self.assertTrue(np.array_equal(a, block_draws(30, [12, 18], draws=50, seed=9)))
        self.assertFalse(np.array_equal(a, block_draws(30, [12, 18], draws=50, seed=10)))

    def test_a_site_without_target_days_is_refused(self):
        from mizan.opsd_evaluation import paired
        with self.assertRaises(Refused) as cm:
            paired(np.array([]), [])
        self.assertEqual(cm.exception.code, "OPSD_NO_DAYS")


if __name__ == "__main__":
    unittest.main()
