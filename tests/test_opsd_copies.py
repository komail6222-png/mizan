"""The separate copy check of the OPSD evaluation (scripts/opsd_copy_check.py): clean days that repeat, hour by hour,
the day one or two weeks earlier, and the published measures recomputed without them."""
import unittest
from datetime import date, timedelta

import numpy as np


def site_hours(days, start=date(2016, 6, 1), seed=3):
    from mizan import opsd
    rng = np.random.default_rng(seed)
    hours = {}
    for k in range(days):
        for t in opsd.local_day_hours(start + timedelta(days=k)):
            hours[t] = (round(float(rng.uniform(20, 60)), 3), 0.0)
    return hours


def copy_day(hours, src, dst):
    from mizan import opsd
    for a, b in zip(opsd.local_day_hours(src), opsd.local_day_hours(dst)):
        hours[b] = hours[a]


class WeekCopyTests(unittest.TestCase):
    def test_a_day_that_repeats_one_or_two_weeks_earlier_is_found(self):
        from mizan import opsd
        from scripts.opsd_copy_check import week_copies
        start = date(2016, 6, 1)
        hours = site_hours(30)
        copy_day(hours, start + timedelta(days=3), start + timedelta(days=10))    # one week later
        copy_day(hours, start + timedelta(days=5), start + timedelta(days=19))    # two weeks later
        clean = opsd.clean_days(hours)
        found = week_copies(hours, clean)
        self.assertEqual(found, {start + timedelta(days=10): 7, start + timedelta(days=19): 14})

    def test_the_previous_day_rule_is_left_to_the_sealed_check(self):
        from mizan import opsd
        from scripts.opsd_copy_check import week_copies
        start = date(2016, 6, 1)
        hours = site_hours(20)
        copy_day(hours, start + timedelta(days=8), start + timedelta(days=9))     # a copy of the day before
        clean = opsd.clean_days(hours)
        self.assertNotIn(start + timedelta(days=9), clean)                        # the sealed rule already drops it
        self.assertEqual(week_copies(hours, clean), {})


class SensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_opsd_evaluation import opsd_record
        cls.record, _ = opsd_record()

    def test_runs_split_where_days_are_removed(self):
        from scripts.opsd_copy_check import split_runs
        rows = [{"date": d, "run": r} for d, r in [("2016-03-01", 0), ("2016-03-02", 0), ("2016-03-03", 0), ("2016-03-05", 1), ("2016-03-06", 1)]]
        self.assertEqual(split_runs(rows, {"2016-03-02"}), ([rows[0], rows[2], rows[3], rows[4]], [1, 1, 2]))

    def test_sensitivity_without_removed_days(self):
        from scripts.opsd_copy_check import sensitivity
        site = sorted(self.record["sites"])[0]
        rows = self.record["sites"][site]["rows"]
        removed = {rows[1]["date"]}
        result = sensitivity(self.record, {site: removed}, draws=50)
        s = result["sites"][site]
        self.assertEqual(s["removed_target_days"], 1)
        self.assertEqual(s["target_days"], len(rows) - 1)
        kept = [r for r in rows if r["date"] not in removed]
        self.assertAlmostEqual(s["forecast"]["seasonal_naive_mae_kw"], float(np.mean([r["naive_mae"] for r in kept])), places=12)
        self.assertEqual({(p["a"], p["b"]) for p in s["accuracy"]}, {("ridge_mae", "naive_mae"), ("chronos_mae", "ridge_mae")})
        self.assertTrue(all(p["reading"] in ("higher", "lower", "not resolved") for p in s["accuracy"] + s["paired"]))
        self.assertIn("separate check", result["note"])
        self.assertEqual(set(result["pooled"]), {"share", "paired", "accuracy"})
        # A site with nothing removed keeps its published means.
        other = sorted(self.record["sites"])[1]
        self.assertEqual(result["sites"][other]["removed_target_days"], 0)
        self.assertAlmostEqual(result["sites"][other]["forecast"]["ridge_mae_kw"],
                               self.record["sites"][other]["forecast"]["ridge_mae_kw"], places=12)


if __name__ == "__main__":
    unittest.main()
