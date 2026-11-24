"""Evaluation harnesses with injectable components, checked against the shipped results (torch-free)."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from mizan import evaluation, examples, hf_evaluation as hf
from mizan.basira import engine as b
from mizan.rasid.forecast import records, vintage

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = json.loads((ROOT / "reports/evaluation/basira.json").read_text())
RASID = json.loads((ROOT / "reports/evaluation/rasid.json").read_text())
QUICK = dict(draws=3, sizes=(24,), pools=(60, 60, 20))


class BasiraHarnessTests(unittest.TestCase):
    def test_descriptor_reproduces_shipped_frozen_test(self):
        r = hf.basira_hf(b.features, b.DESCRIPTOR, draws=3, sizes=(24,), pools=(60, 60, 20))
        self.assertEqual((r["frozen_test"]["detected"], r["frozen_test"]["false_alerts"]), (40, 8))
        self.assertEqual(r["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])

    def test_gate_reports_each_mismatch(self):
        bad = {"frozen_test": {"detected": 40, "false_alerts": 9, "bank_digest": SHIPPED["bank_digest"]}, "calibration_study": SHIPPED["calibration_study"]}
        g = hf.basira_gate(bad, SHIPPED)
        self.assertFalse(g["passed"]); self.assertIn("false_alerts", g["mismatches"])

    def test_harness_mirrors_the_shipped_functions_item_for_item(self):
        r = hf.basira_hf(b.features, b.DESCRIPTOR, **QUICK)
        self.assertEqual(r["profile"], b.DESCRIPTOR)
        self.assertEqual(r["patches_averaged"], 4)  # top 2.5% of the 12 x 12 descriptor grid
        t = r["frozen_test"]
        self.assertEqual((t["test_items"], t["calibration_m"]), (SHIPPED["test_items"], SHIPPED["calibration_m"]))
        self.assertEqual([(x["seed"], x["condition"], x["p_value"], x["flag"]) for x in t["rows"]],
                         [(x["seed"], x["condition"], x["p_value"], x["flag"]) for x in SHIPPED["rows"]])
        self.assertEqual(r["calibration_study"], evaluation.calibration_study(**QUICK))
        # A reduced run never passes the gate: only its calibration study differs from the shipped full-size one.
        g = hf.basira_gate(r, SHIPPED)
        self.assertFalse(g["passed"]); self.assertIn("calibration_study.pools", g["mismatches"])
        self.assertTrue(all(n.startswith(("calibration_study.", "shipped_calibration.", "rows[")) for n in g["mismatches"]), g["mismatches"])

    def test_every_scored_image_goes_through_the_given_feature_function(self):
        calls = []
        def counted(im):
            calls.append(1); return b.features(im)
        r = hf.basira_hf(counted, b.DESCRIPTOR, **QUICK)
        # 10 reference + 24 calibration images in fit, 80 frozen items, then every pool image.
        self.assertEqual(len(calls), 10 + 24 + 80 + sum(QUICK["pools"]))
        self.assertEqual(r["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])
        other = hf.basira_hf(b.features, "test/another-profile", **QUICK)
        self.assertEqual(other["profile"], "test/another-profile")
        self.assertNotEqual(other["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])
        self.assertEqual(other["calibration_study"]["bank_digest"], other["frozen_test"]["bank_digest"])

    def test_gate_passes_the_shipped_values_and_names_every_check(self):
        g = hf.basira_gate(self.shipped_as_result(), SHIPPED)
        self.assertTrue(g["passed"]); self.assertEqual(g["mismatches"], [])
        # 3 frozen counts + 80 frozen rows x 4 fields + 6 study settings + 7 shipped-calibration fields + 32 row fields.
        self.assertEqual(len(g["checked"]), 368); self.assertEqual(len(set(g["checked"])), 368)
        self.assertEqual(g["checked"][:3], ["detected", "false_alerts", "bank_digest"])
        self.assertEqual([n for n in g["checked"] if n.startswith("frozen_test.rows[seed=9179].")],
                         ["frozen_test.rows[seed=9179]." + k for k in ("seed", "condition", "p_value", "flag")])
        self.assertIn("shipped_calibration.long_run_false_alert_estimate", g["checked"])
        self.assertIn("rows[m=100].pac_share_of_banks_above_alpha", g["checked"])
        # A row field that is absent on one side is a mismatch, even where the other side holds null.
        study = json.loads(json.dumps(SHIPPED["calibration_study"])); del study["rows"][0]["pac_rank"]
        g = hf.basira_gate({**self.shipped_as_result(), "calibration_study": study}, SHIPPED)
        self.assertEqual(g["mismatches"], ["rows[m=24].pac_rank"])

    def test_gate_fails_when_two_frozen_items_swap_flags(self):
        same = self.shipped_as_result(); rows = same["frozen_test"]["rows"]
        hit = next(x for x in rows[:40] if x["flag"]); miss = next(x for x in rows[:40] if not x["flag"])
        hit["flag"], miss["flag"] = False, True  # the false-alert count is unchanged
        g = hf.basira_gate(same, SHIPPED)
        self.assertFalse(g["passed"])
        self.assertEqual(sorted(g["mismatches"]), sorted(f"frozen_test.rows[seed={x['seed']}].flag" for x in (hit, miss)))

    @staticmethod
    def shipped_as_result():
        """The shipped numbers in the harness's result shape (a fresh copy each call)."""
        frozen = {k: SHIPPED[k] for k in ("detected", "false_alerts", "bank_digest", "rows")}
        return json.loads(json.dumps({"frozen_test": frozen, "calibration_study": SHIPPED["calibration_study"]}))


def stub(load, pv, levels, seen=[]):
    seen.append(len(load)); base = np.stack([load[-24:], pv[-24:]], axis=1)
    return base[:, :, None] + np.array(levels)[::-1] - 0.5   # deliberately unsorted along levels


def naive_stub(load, pv, levels):
    """Last week's value for each of the 24 target hours, repeated across levels: the shipped seasonal-naive path."""
    return np.repeat(np.stack([load[-168:-144], pv[-168:-144]], axis=1)[:, :, None], len(levels), axis=2)


def _scaled(shipped, policy, factor):
    """The shipped ridge numbers in rasid_hf's result shape (a fresh copy), with one policy's total cost times factor."""
    r = json.loads(json.dumps({"target_days": shipped["target_days"], "origins": shipped["origins"],
                               "forecast": {k: shipped[k] for k in ("ridge_mae_kw", "seasonal_naive_mae_kw")},
                               "decision_quality": shipped["decision_quality"]}))
    r["decision_quality"][policy]["total_cost"] *= factor
    return r


class RasidHarnessTests(unittest.TestCase):
    def test_two_days_shapes_and_sorted_quantiles(self):
        r = hf.rasid_hf(stub, days=2)
        self.assertEqual(len(r["origins"]), 2)
        self.assertEqual(r["decision_quality"]["chronos"]["verified_feasible_plans"], 2)
        self.assertGreater(r["forecast"]["pv_values_clipped"], 0)   # the stub's night-time solar goes negative
        self.assertEqual(list(r["decision_quality"]), list(hf.POLICIES_HF))
        self.assertEqual(hf.POLICIES_HF, evaluation.POLICIES + ("chronos", "chronos_risk_neutral"))
        self.assertEqual([(c["policy"], c["reference"]) for c in r["paired_comparisons"]],
                         [("chronos_risk_neutral", "candidate_risk_neutral"), ("chronos_risk_neutral", "seasonal_naive"), ("chronos", "candidate")])
        for k in ("ridge_mae_kw", "chronos_mae_kw", "seasonal_naive_mae_kw", "ridge_mean_pinball", "ridge_truncated_crps",
                  "ridge_coverage_80_load", "chronos_mean_pinball", "chronos_truncated_crps", "chronos_coverage_80_load"):
            self.assertIsInstance(r["forecast"][k], float, k)
        self.assertEqual(r["forecast"]["shared_levels"], [0.1, 0.5, 0.9])
        for text in ("0.1, 0.3, 0.5, 0.7 and 0.9", "4 − j", "0.2", "sorted across levels", "clipped at zero"):
            self.assertIn(text, r["scenario_rule"])

    def test_context_stops_before_origin_and_at_a_gap(self):
        rows = records(examples.electricity(44)); origin = datetime(2026, 2, 12, tzinfo=timezone.utc)
        gap = origin - timedelta(hours=30); rows = [r for r in rows if r[0] != gap]
        load, pv, n, last = hf.chronos_context(rows, origin)
        self.assertLess(last, origin); self.assertGreater(last - timedelta(hours=n), gap - timedelta(hours=1))
        self.assertEqual(len(load), n); self.assertEqual(len(pv), n)

    def test_context_is_the_vintage_run_ending_the_hour_before_the_origin(self):
        rows = records(examples.electricity(44)); origin = datetime(2026, 2, 12, tzinfo=timezone.utc)
        gap = origin - timedelta(hours=30); hist = vintage([r for r in rows if r[0] != gap], origin)
        load, pv, n, last = hf.chronos_context([r for r in rows if r[0] != gap], origin)
        self.assertEqual((n, last), (29, origin - timedelta(hours=1)))
        hours = [last - timedelta(hours=n - 1 - h) for h in range(n)]
        self.assertEqual(load.tolist(), [hist[t][0] for t in hours]); self.assertEqual(pv.tolist(), [hist[t][1] for t in hours])
        # A later revision of a past hour is only seen once it is known.
        late = (origin - timedelta(hours=5), origin + timedelta(hours=1), 999.0, 0.0)
        self.assertEqual(hf.chronos_context(rows + [late], origin)[0].tolist(), hf.chronos_context(rows, origin)[0].tolist())

    def test_harness_feeds_only_the_vintage_and_mirrors_the_shipped_ridge_rows(self):
        contexts = []
        def recording(load, pv, levels):
            contexts.append((np.array(load), np.array(pv))); return naive_stub(load, pv, levels)
        r = hf.rasid_hf(recording, days=2)
        actual = {t: (l, p) for t, _, l, p in records(examples.electricity(44))}
        for i, row in enumerate(r["origins"]):
            origin = datetime(2026, 2, 12 + i, tzinfo=timezone.utc); n = (42 + i) * 24
            load, pv = contexts[i]; hours = [origin - timedelta(hours=n - h) for h in range(n)]   # ends at origin - 1 h
            self.assertEqual(row["context_hours"], n)
            self.assertEqual(load.tolist(), [actual[t][0] for t in hours]); self.assertEqual(pv.tolist(), [actual[t][1] for t in hours])
            # Last week's values through the median reproduce the shipped seasonal-naive error exactly.
            self.assertAlmostEqual(row["chronos_mae"], row["naive_mae"], delta=1e-12)
            want = RASID["origins"][i]
            self.assertEqual((row["date"], row["coverage_80_load"]), (want["date"], want["coverage_80_load"]))
            for k in ("ridge_mae", "naive_mae"):
                self.assertAlmostEqual(row[k], want[k], delta=1e-9)
            for k in hf.POLICIES_HF:
                self.assertEqual(row[k + "_status"], "VERIFIED_FEASIBLE")
        self.assertEqual(r["forecast"]["pv_values_clipped"], 0)

    def test_chronos_policies_get_the_paired_sorted_clipped_scenarios(self):
        calls, contexts = [], []
        def recorder(name, f, y, state, peak):
            calls.append((name, f, y, state, peak)); return evaluation.policy_problem(name, f, y, state, peak)
        def recording(load, pv, levels):
            contexts.append((np.array(load), np.array(pv))); return stub(load, pv, levels)
        with mock.patch.object(hf, "policy_problem", side_effect=recorder):
            r = hf.rasid_hf(recording, days=1)
        self.assertEqual([c[0] for c in calls], list(evaluation.POLICIES) + ["candidate", "candidate_risk_neutral"])
        ridge, chronos, y = calls[0][1], calls[5][1], calls[0][2]
        self.assertEqual(calls[6][1], chronos); self.assertEqual([c[3:] for c in calls[5:]], [(40.0, 0.0)] * 2)
        rest = lambda f: {k: v for k, v in f.items() if k not in ("weights", "scenarios")}
        self.assertEqual(rest(chronos), rest(ridge)); self.assertEqual(chronos["weights"], [0.2] * 5)
        load, pv = contexts[0]; shift = (-.4, -.2, 0., .2, .4)   # the stub's offsets once sorted along levels
        want = [[[load[h - 24] + shift[j], max(0., pv[h - 24] - shift[j])] for h in range(24)] for j in range(5)]
        np.testing.assert_allclose(chronos["scenarios"], want, rtol=0, atol=1e-12)
        q = np.sort(stub(load, pv, hf.CHRONOS_LEVELS), axis=-1)
        self.assertEqual(r["origins"][0]["pv_values_clipped"], int((q[:, 1, :] < 0).sum()))
        self.assertEqual(r["origins"][0]["chronos_mae"], float(np.mean(abs(q[:, 0, 2] - y[:, 0]))))

    def test_gate_tolerance(self):
        shipped = json.loads((ROOT / "reports/evaluation/rasid.json").read_text())
        ok = hf.rasid_gate(_scaled(shipped, "candidate", 1.0001), shipped); bad = hf.rasid_gate(_scaled(shipped, "candidate", 1.01), shipped)
        self.assertTrue(ok["passed"]); self.assertFalse(bad["passed"])

    def test_gate_names_every_check_and_applies_each_tolerance(self):
        g = hf.rasid_gate(_scaled(RASID, "candidate", 1.0), RASID)
        self.assertTrue(g["passed"]); self.assertEqual(g["mismatches"], [])
        # target_days, 2 mean errors, 5 policies x (total, verified plans), 60 origins x (date, 2 errors, coverage).
        self.assertEqual(len(g["checked"]), 1 + 2 + 10 + 240); self.assertEqual(len(set(g["checked"])), 253)
        self.assertIn("origins[date=2026-04-12].coverage_80_load", g["checked"])
        for policy, inside, outside in (("candidate", 1.0004, 1.0006), ("candidate_risk_neutral", 0.9996, 0.9994),
                                        ("seasonal_naive", 1 + 9e-7, 1 + 2e-6), ("perfect_foresight", 1 - 9e-7, 1 - 2e-6),
                                        ("no_battery", 1 + 9e-7, 1 + 2e-6)):
            self.assertTrue(hf.rasid_gate(_scaled(RASID, policy, inside), RASID)["passed"], policy)
            self.assertEqual(hf.rasid_gate(_scaled(RASID, policy, outside), RASID)["mismatches"], [f"decision_quality.{policy}.total_cost"])
        day = RASID["origins"][3]["date"]
        r = _scaled(RASID, "candidate", 1.0); r["origins"][3]["ridge_mae"] += 5e-10
        self.assertTrue(hf.rasid_gate(r, RASID)["passed"])
        r["origins"][3]["ridge_mae"] += 1e-9; r["forecast"]["seasonal_naive_mae_kw"] += 2e-9; r["origins"][3]["coverage_80_load"] -= 1 / 24
        self.assertEqual(hf.rasid_gate(r, RASID)["mismatches"],
                         ["forecast.seasonal_naive_mae_kw", f"origins[date={day}].ridge_mae", f"origins[date={day}].coverage_80_load"])
        # A value missing on one side never matches; a shorter run fails on every absent row.
        r = _scaled(RASID, "candidate", 1.0); del r["decision_quality"]["no_battery"]["verified_feasible_plans"]
        self.assertEqual(hf.rasid_gate(r, RASID)["mismatches"], ["decision_quality.no_battery.verified_feasible_plans"])
        r = _scaled(RASID, "candidate", 1.0); r["origins"] = r["origins"][:2]; r["target_days"] = 2
        g = hf.rasid_gate(r, RASID)
        self.assertFalse(g["passed"]); self.assertEqual(len(g["mismatches"]), 1 + 58 * 4)
        self.assertFalse(hf.rasid_gate({}, RASID)["passed"])

    def test_seven_policy_summary_matches_the_shipped_one(self):
        rows = RASID["origins"]
        self.assertEqual(hf._decision_summary(rows, evaluation.POLICIES), evaluation.decision_summary(rows))
        self.assertEqual(hf._decision_summary(rows, evaluation.POLICIES), RASID["decision_quality"])


if __name__ == "__main__":
    unittest.main()
