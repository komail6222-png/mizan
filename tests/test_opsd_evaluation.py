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


def stub_forecaster(load, pv, levels):
    """A quantile forecaster for tests: yesterday's last 24 hours, spread ±5% across the levels."""
    base = np.stack([np.asarray(load)[-24:], np.asarray(pv)[-24:]], axis=1)
    spread = 1 + 0.05 * np.linspace(-1, 1, len(levels))
    return base[..., None] * spread


class GeneratedSite:
    """The generated site as rasid_hf reads it: 102 days of examples.electricity, UTC days from 2026-02-12."""
    def __init__(self):
        from datetime import datetime, timedelta, timezone
        from mizan import examples
        from mizan.rasid.forecast import records
        self.raw = examples.electricity(102)
        rows = records(self.raw)
        self.actual = {t: (l, p) for t, k, l, p in rows}
        self.origin0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.td = timedelta

    def origins(self, first, count):
        return [self.origin0 + self.td(days=42 + i) for i in range(first, first + count)]

    def plan(self, runs, forecaster=stub_forecaster, rows_at=None):
        from mizan import examples
        from mizan.opsd_evaluation import plan_days
        return plan_days(runs, rows_at or (lambda o: self.raw), lambda t: self.actual[t], examples.settings(24), forecaster,
                         grid_at=lambda o: [o + self.td(hours=h) for h in range(24)], month_of=lambda o: (o.year, o.month))


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = GeneratedSite()

    def test_day_problem_equals_the_shipped_problem_at_scale_one(self):
        from mizan import evaluation, examples
        from mizan.opsd_evaluation import day_problem
        from mizan.rasid.forecast import forecast, records
        origin = self.site.origins(0, 1)[0]
        grid = [origin + self.site.td(hours=h) for h in range(24)]
        f = forecast(records(self.site.raw), origin, grid, 4)
        y = np.array([self.site.actual[t] for t in grid])
        for name in evaluation.POLICIES:
            with self.subTest(name):
                self.assertEqual(day_problem(name, f, y, 40., 3., examples.settings(24)),
                                 evaluation.policy_problem(name, f, y, 40., 3.))

    def test_plan_days_reproduces_rasid_hf_with_the_same_forecaster(self):
        from mizan.hf_evaluation import POLICIES_HF, rasid_hf
        shipped = rasid_hf(stub_forecaster, days=4)["origins"]
        ours = self.site.plan([self.site.origins(0, 4)])
        self.assertEqual(ours["run_lengths"], [4])
        fields = ["date", "ridge_mae", "naive_mae", "coverage_80_load", "ridge_mean_pinball", "chronos_mae", "context_hours",
                  *[k + s for k in POLICIES_HF for s in ("_cost", "_status")]]
        self.assertEqual([{k: r[k] for k in fields} for r in ours["rows"]], [{k: r[k] for k in fields} for r in shipped])

    def test_refusal_drops_the_day_for_all_and_restarts_the_run(self):
        from unittest import mock
        import mizan.opsd_evaluation as oe
        from mizan.hf_evaluation import POLICIES_HF
        real = oe.solve
        n = len(POLICIES_HF)
        origins = self.site.origins(0, 5)
        for refused_days, rows_left, runs in (((2,), [0, 1, 3, 4], [2, 2]), ((2, 3), [0, 1, 4], [2, 1]), ((0,), [1, 2, 3, 4], [4])):
            calls = {"k": 0}
            def solve(p, _days=refused_days):
                k = calls["k"]
                calls["k"] += 1
                if k // n in _days and k % n == 1:
                    raise Refused("NO_INCUMBENT", "stub")
                return real(p)
            with self.subTest(refused=refused_days), mock.patch.object(oe, "solve", solve):
                result = self.site.plan([origins])
                self.assertEqual([r["date"] for r in result["rows"]], [origins[i].date().isoformat() for i in rows_left])
                self.assertEqual(result["run_lengths"], runs)
                self.assertEqual(result["planning_refused"], [{"date": origins[d].date().isoformat(), "policy": POLICIES_HF[1],
                                                               "cause": "NO_INCUMBENT"} for d in refused_days])
        # The day after a refusal starts afresh: the same costs as a run that begins on that day.
        calls = {"k": 0}
        def solve_once(p):
            k = calls["k"]
            calls["k"] += 1
            if k // n == 2 and k % n == 1:
                raise Refused("NO_INCUMBENT", "stub")
            return real(p)
        with mock.patch.object(oe, "solve", solve_once):
            after = self.site.plan([origins])["rows"][2:]
        fresh = self.site.plan([origins[3:]])["rows"]
        self.assertEqual([[r[k + "_cost"] for k in POLICIES_HF] for r in after], [[r[k + "_cost"] for k in POLICIES_HF] for r in fresh])
        self.assertTrue(all(after[0][k + "_start_peak"] == 0 for k in POLICIES_HF))

    def test_ridge_refusal_is_counted_not_fatal(self):
        origins = self.site.origins(0, 3)
        short = lambda o: self.site.raw[-24 * 5:] if o == origins[1] else self.site.raw   # noqa: E731
        result = self.site.plan([origins], rows_at=short)
        self.assertEqual(result["ridge_refused"], {"HISTORY": 1})
        self.assertEqual([r["date"] for r in result["rows"]], [origins[0].date().isoformat(), origins[2].date().isoformat()])
        self.assertEqual(result["run_lengths"], [1, 1])

    def test_month_start_resets_only_the_peak(self):
        from mizan.hf_evaluation import POLICIES_HF
        origins = self.site.origins(15, 4)           # 2026-02-27 .. 2026-03-02
        rows = self.site.plan([origins])["rows"]
        march = [r for r in rows if r["date"] >= "2026-03-01"]
        self.assertTrue(all(march[0][k + "_start_peak"] == 0 for k in POLICIES_HF))
        self.assertTrue(all(rows[1][k + "_start_peak"] > 0 for k in POLICIES_HF if k != "no_battery" or True))
        self.assertEqual(march[0]["candidate_start_charge"], rows[1]["candidate_start_charge"])   # the charge carries
        self.assertEqual(rows[0]["run"], march[0]["run"])

    def test_generated_gate_fails_closed(self):
        import copy
        import json
        from pathlib import Path
        from mizan.opsd_evaluation import gate_check
        root = Path(__file__).resolve().parents[1]
        ridge = json.loads((root / "reports/evaluation/rasid.json").read_text(encoding="utf-8"))
        hf = json.loads((root / "reports/hf/rasid.json").read_text(encoding="utf-8"))
        by_date = {r["date"]: r for r in hf["model"]["origins"]}
        result = {"target_days": ridge["target_days"],
                  "forecast": {"ridge_mae_kw": ridge["ridge_mae_kw"], "seasonal_naive_mae_kw": ridge["seasonal_naive_mae_kw"],
                               **hf["model"]["forecast"]},
                  "decision_quality": {**ridge["decision_quality"], **hf["model"]["decision_quality"]},
                  "origins": [{**r, **by_date[r["date"]]} for r in ridge["origins"]]}
        self.assertTrue(gate_check(result, ridge, hf)["passed"])
        bad = copy.deepcopy(ridge)
        bad["decision_quality"]["candidate"]["total_cost"] *= 1.01
        gate = gate_check(result, bad, hf)
        self.assertFalse(gate["passed"])
        self.assertIn("decision_quality.candidate.total_cost", gate["ridge"]["mismatches"])
        bad_hf = copy.deepcopy(hf)
        bad_hf["model"]["decision_quality"]["chronos"]["total_cost"] *= 1.01
        gate = gate_check(result, ridge, bad_hf)
        self.assertFalse(gate["passed"])
        self.assertIn("decision_quality.chronos.total_cost", gate["chronos"]["mismatches"])


FORBIDDEN = ("euro", "EUR", "€", "saving", "money")


def opsd_record(path=None):
    """A complete record on the stand-in file with the stub forecaster, quick mode."""
    import tempfile
    from pathlib import Path
    from mizan.opsd_evaluation import evaluate_opsd
    from tests.opsd_fixture import make_opsd_csv
    tmp = tempfile.mkdtemp()
    csv_path = make_opsd_csv(Path(tmp) / "household.csv")
    return evaluate_opsd(csv_path, stub_forecaster, quick=True), csv_path


class RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record, cls.csv = opsd_record()

    def test_record_shape(self):
        from mizan import opsd
        from mizan.hf_evaluation import POLICIES_HF
        from mizan.opsd_evaluation import ACCURACY, COMPARISONS
        r = self.record
        self.assertEqual(set(r), {"identity", "settings", "quick", "notes", "sites", "pooled", "scenario_rule"})
        self.assertEqual(r["identity"]["file_sha256"], opsd.FILE_SHA256)
        self.assertEqual(set(r["sites"]), set(opsd.SITES))
        for site, s in r["sites"].items():
            with self.subTest(site):
                self.assertEqual(set(s), {"building", "base", "counts", "export_gate", "forecast", "decision_quality", "share",
                                          "paired", "accuracy", "plans", "run_lengths", "rows"})
                self.assertEqual(s["counts"]["target_days"], len(s["rows"]))
                self.assertEqual(sum(s["run_lengths"]), len(s["rows"]))
                self.assertGreater(len(s["rows"]), 0)
                self.assertEqual(set(s["share"]), set(POLICIES_HF))
                self.assertEqual([(p["a"], p["b"]) for p in s["paired"]], [tuple(c) for c in COMPARISONS])
                self.assertEqual([(p["a"], p["b"]) for p in s["accuracy"]], [tuple(c) for c in ACCURACY])
                self.assertTrue(all(p["reading"] in ("higher", "lower", "not resolved") for p in s["paired"] + s["accuracy"]))
                self.assertEqual(s["base"]["scale"] > 0, True)
        self.assertEqual(set(r["pooled"]), {"share", "paired", "accuracy", "normalisers"})

    def test_export_gate_stops_before_forecasting(self):
        from unittest import mock
        from mizan import opsd
        from mizan.opsd_evaluation import evaluate_opsd
        calls = []
        def forecaster(*a):
            calls.append(a)
            return stub_forecaster(*a)
        failing = {"sunny_hours": 3, "low_import_hours": 1, "passed": False, "solar_threshold_kw": 1., "import_threshold_kw": 1.}
        with mock.patch.object(opsd, "export_gate", return_value=failing), self.assertRaises(Refused) as cm:
            evaluate_opsd(self.csv, forecaster, quick=True)
        self.assertEqual(cm.exception.code, "OPSD_EXPORT")
        self.assertEqual(calls, [])

    def test_pooled_uses_fixed_normalisers(self):
        norm = self.record["pooled"]["normalisers"]
        for site, s in self.record["sites"].items():
            with self.subTest(site):
                rows = s["rows"]
                self.assertAlmostEqual(norm[site]["mean_daily_no_battery_cost"], float(np.mean([r["no_battery_cost"] for r in rows])), places=9)
                self.assertAlmostEqual(norm[site]["mean_load_kw"], s["forecast"]["mean_load_kw"], places=9)
        # Each pooled comparison is the equal-weight mean of the sites' normalised mean differences.
        first = self.record["pooled"]["paired"][0]
        want = np.mean([np.mean([r[first["a"] + "_cost"] - r[first["b"] + "_cost"] for r in s["rows"]]) / norm[site]["mean_daily_no_battery_cost"]
                        for site, s in sorted(self.record["sites"].items())])
        self.assertAlmostEqual(first["mean"], float(want), places=12)

    def test_no_currency_in_notes(self):
        text = " ".join(self.record["notes"])
        for word in FORBIDDEN:
            self.assertNotIn(word, text.replace("Europe", ""))
        self.assertIn("reconstructed", text)
        self.assertIn("illustrative tariff units", text)


if __name__ == "__main__":
    unittest.main()
