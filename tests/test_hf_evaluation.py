"""Evaluation harnesses with injectable components, checked against the shipped results (torch-free)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from mizan import evaluation, examples, hf_evaluation as hf, models
from mizan.basira import engine as b
from mizan.core.contract import Refused, canonical
from mizan.rasid.forecast import records, vintage
from mizan.rasid.solver import solve
from mizan.sanad import engine as s
from scripts import evaluate_hf

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
        for text in ("0.1, 0.3, 0.5, 0.7 and 0.9", "4 − j", "0.2", "sorted across levels", "clipped at zero",
                     "Load quantiles are not clipped"):
            self.assertIn(text, r["scenario_rule"])
        self.assertIn("must end at the hour before the origin", r["forecast"]["note"])

    def test_mirror_equals_the_shipped_function_in_this_environment(self):
        r = hf.rasid_hf(naive_stub, days=2); e = evaluation.energy_evaluation(days=2)
        shared = set(e["origins"][0]) - {"mean_pinball", "truncated_crps", "domain"}   # the shipped scores use all five ridge levels
        self.assertEqual(set(r["origins"][0]) & set(e["origins"][0]), shared); self.assertEqual(len(shared), 4 + 2 * 5)
        self.assertEqual([{k: x[k] for k in shared} for x in r["origins"]], [{k: x[k] for k in shared} for x in e["origins"]])
        self.assertEqual({k: r["decision_quality"][k] for k in evaluation.POLICIES}, e["decision_quality"])
        self.assertEqual((r["forecast"]["ridge_mae_kw"], r["forecast"]["seasonal_naive_mae_kw"]), (e["ridge_mae_kw"], e["seasonal_naive_mae_kw"]))

    def test_refuses_a_misaligned_context_and_a_malformed_forecast(self):
        real = hf.chronos_context
        with mock.patch.object(hf, "chronos_context", side_effect=lambda rows, origin: real(rows, origin - timedelta(hours=1))):
            with self.assertRaises(Refused) as caught:
                hf.rasid_hf(naive_stub, days=1)   # the run would end two hours before the origin
        self.assertEqual(caught.exception.code, "CONTEXT")
        def poisoned(value):
            def forecaster(load, pv, levels):
                q = naive_stub(load, pv, levels).astype(float); q[5, 1, 2] = value; return q
            return forecaster
        for name, bad in (("too few levels", lambda load, pv, levels: naive_stub(load, pv, levels)[:, :, :4]),
                          ("23 hours", lambda load, pv, levels: naive_stub(load, pv, levels)[:23]),
                          ("one series", lambda load, pv, levels: naive_stub(load, pv, levels)[:, :1]),
                          ("nan", poisoned(np.nan)), ("inf", poisoned(-np.inf))):
            with self.subTest(name), self.assertRaises(Refused) as caught:
                hf.rasid_hf(bad, days=1)
            self.assertEqual(caught.exception.code, "FORECAST")
        def negative_load(load, pv, levels):
            q = naive_stub(load, pv, levels).astype(float); q[3, 0, 0] = -1.0; return q
        with self.assertRaises(Refused) as caught:   # load is not clipped (SCENARIO_RULE): the planner's input check stops it
            hf.rasid_hf(negative_load, days=1)
        self.assertEqual(caught.exception.code, "INPUT_RANGE")

    def test_each_policy_carries_its_own_state_and_peak(self):
        problems, plans, peaks = [], [], []
        def problem(name, f, y, state, peak):
            problems.append((state, peak)); return evaluation.policy_problem(name, f, y, state, peak)
        def solving(p):
            plans.append(solve(p)); return plans[-1]
        def settling(plan, y, p):
            out = evaluation.realized(plan, y, p); peaks.append(out[1])
            if p["assets"]["capacity"]:   # most plans end at the 40 kWh terminal bound: make each end state distinct
                plan["soc"][-1] += 0.01 * len(peaks)
            return out
        with mock.patch.object(hf, "policy_problem", side_effect=problem), mock.patch.object(hf, "solve", side_effect=solving), \
             mock.patch.object(hf, "realized", side_effect=settling):
            hf.rasid_hf(stub, days=3)
        n = len(hf.POLICIES_HF); self.assertEqual(len(problems), 3 * n)
        self.assertEqual(problems[:n], [(0.0 if k == "no_battery" else 40.0, 0.0) for k in hf.POLICIES_HF])
        for day in (1, 2):   # 13 and 14 February, the same month: each policy continues from its own earlier days
            for i, label in enumerate(hf.POLICIES_HF):
                self.assertEqual(problems[day * n + i], (plans[(day - 1) * n + i]["soc"][-1], max(peaks[i::n][:day])), (day, label))
        chronos, candidate = hf.POLICIES_HF.index("chronos"), hf.POLICIES_HF.index("candidate")
        self.assertNotEqual(problems[n + chronos], problems[n + candidate])   # a shared state or peak would show here

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
        # The tolerance is per field: the seasonal-naive error has it, the coverage and the date do not.
        r = _scaled(RASID, "candidate", 1.0); r["origins"][5]["naive_mae"] += 5e-10; r["origins"][5]["coverage_80_load"] += 1e-12
        r["origins"][6]["date"] += " "
        self.assertEqual(hf.rasid_gate(r, RASID)["mismatches"],
                         [f"origins[date={RASID['origins'][5]['date']}].coverage_80_load", f"origins[date={RASID['origins'][6]['date']}].date"])
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
        # A Chronos policy is summarised exactly as the shipped function summarises a shipped one. The costs are
        # crossed (chronos gets seasonal_naive's, chronos_risk_neutral gets candidate's) so a missing remap shows.
        copied = [{**r, "chronos_cost": r["seasonal_naive_cost"], "chronos_status": r["seasonal_naive_status"],
                   "chronos_risk_neutral_cost": r["candidate_cost"], "chronos_risk_neutral_status": r["candidate_status"]}
                  for r in rows]
        s = hf._decision_summary(copied)
        self.assertEqual(list(s), list(hf.POLICIES_HF))
        self.assertEqual({k: s[k] for k in evaluation.POLICIES}, RASID["decision_quality"])
        self.assertEqual((s["chronos"], s["chronos_risk_neutral"]),
                         (RASID["decision_quality"]["seasonal_naive"], RASID["decision_quality"]["candidate"]))


def _oracle_embedder():
    """Attribute k gets the unit vector e_k: a query naming its labels and the line stating it (label before the
    colon) share it. Every other line gets one more axis, orthogonal to all attribute vectors."""
    attrs = list(s.ATTR); eye = np.eye(len(attrs) + 1)
    def axis(text):
        if text.startswith("query: "):
            found = [i for i, a in enumerate(attrs) if any(label in text for label in s.ATTR[a])]
            assert len(found) == 1, text; return found[0]
        assert text.startswith("passage: "), text
        left = text[len("passage: "):].partition(":")[0].strip()
        return next((i for i, a in enumerate(attrs) if left in s.ATTR[a]), len(attrs))
    return lambda strings: eye[[axis(t) for t in strings]]


def _constant_embedder(strings):
    return np.ones((len(strings), 3))   # every cosine is 1: the ranking is document order


KINDS = ("bilingual", "en", "ar")


def _queries(attr):
    return {"bilingual": " ".join(s.ATTR[attr]), "en": s.ATTR[attr][0], "ar": s.ATTR[attr][1]}


class SanadHarnessTests(unittest.TestCase):
    def test_gold_is_defined_by_the_grammar(self):
        items = hf.retrieval_set(10, seed=7)
        self.assertEqual({i["language"] for i in items}, {"en", "ar"})
        for it in items:
            claims = s.parse_document(it["doc"], it["model"])["claims"]
            self.assertEqual({c["attribute"] for c in claims}, set(it["gold"]))
        self.assertEqual(hf.retrieval_set_digest(items), hf.retrieval_set_digest(hf.retrieval_set(10, seed=7)))

    def test_oracle_embedder_scores_one_and_lexical_is_bounded(self):
        r = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        self.assertEqual(r["metrics"]["e5"]["bilingual"]["all"]["recall_at_1"], 1.0)
        for v in r["metrics"]["lexical"]["bilingual"]["all"].values(): self.assertTrue(0 <= v <= 1)

    def test_set_layout_one_gold_line_per_attribute_and_fixed_distractors(self):
        items = hf.retrieval_set(12, seed=3)
        self.assertEqual([it["language"] for it in items].count("en"), 6)
        for it in items:
            lang, lines = it["language"], it["lines"]; page, = it["doc"]["pages"]
            self.assertEqual(lines, page.split("\n")); self.assertEqual(len(set(lines)), len(lines))
            self.assertEqual(lines[0], ("Model: " if lang == "en" else "الطراز: ") + it["model"])
            claims = s.parse_document(it["doc"], it["model"])["claims"]
            self.assertEqual(it["gold"], {c["attribute"]: [lines.index(c["quote"])] for c in claims})
            self.assertEqual(len(claims), len(it["gold"])); self.assertTrue(3 <= len(claims) <= 5)
            for attr, (i,) in it["gold"].items():
                label, _, value = lines[i].partition(": ")
                self.assertEqual(label, s.ATTR[attr][0 if lang == "en" else 1])
                if lang == "ar": self.assertFalse(any(c.isascii() and c.isdigit() for c in value), value)
            gold = {i for (i,) in it["gold"].values()}
            distractors = [x for i, x in enumerate(lines) if i and i not in gold]
            self.assertTrue(4 <= len(distractors) <= 6); self.assertLessEqual(set(distractors), set(hf.DISTRACTORS[lang]))
        self.assertNotEqual(hf.retrieval_set_digest(items), hf.retrieval_set_digest(hf.retrieval_set(12, seed=4)))
        self.assertEqual(hf.retrieval_set_digest(items), hf.retrieval_set_digest(hf.retrieval_set(12, seed=3)))

    def test_generator_stops_when_a_distractor_is_a_claim(self):
        claims = [f"Transfer time: {n} ms" for n in range(1, 9)]
        with mock.patch.dict(hf.DISTRACTORS, {"en": claims}), self.assertRaises(Refused) as caught:
            hf.retrieval_set(4, seed=7)
        self.assertEqual(caught.exception.code, "RETRIEVAL_SET")
        heading = ["Model: Other-1"] + hf.DISTRACTORS["en"][1:]   # a second model heading hides the attribute lines
        with mock.patch.dict(hf.DISTRACTORS, {"en": heading}), self.assertRaises(Refused) as caught:
            hf.retrieval_set(20, seed=7)
        self.assertEqual(caught.exception.code, "RETRIEVAL_SET")

    def test_oracle_is_perfect_for_every_query_kind_and_language(self):
        r = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        items = hf.retrieval_set(10, seed=7)
        self.assertEqual((r["n_docs"], r["seed"], r["set_digest"]), (10, 7, hf.retrieval_set_digest(items)))
        self.assertEqual(r["queries"], sum(len(it["gold"]) for it in items))
        self.assertEqual(r["queries_by_language"], {lang: sum(len(it["gold"]) for it in items if it["language"] == lang) for lang in ("en", "ar")})
        self.assertEqual(r["documents"], {"en": 5, "ar": 5})
        self.assertEqual(list(r["metrics"]), ["lexical", "e5"])
        for kind in KINDS:
            for lang in ("en", "ar", "all"):
                self.assertEqual(r["metrics"]["e5"][kind][lang], {"recall_at_1": 1.0, "recall_at_3": 1.0, "mrr_at_4": 1.0})
                self.assertEqual(list(r["metrics"]["lexical"][kind][lang]), ["recall_at_1", "recall_at_3", "mrr_at_4"])
        self.assertEqual(r["lexical_ranker"], "mizan.sanad.engine.retrieve")
        for text in ("parse_document", "top four", "query: ", "passage: ", "document order", "not recomputed"):
            self.assertIn(text, r["protocol"])

    def test_metrics_follow_their_definitions_with_ties_in_document_order(self):
        r = hf.sanad_hf(_constant_embedder, n_docs=10, seed=7)
        items = hf.retrieval_set(10, seed=7)
        for lang in ("en", "ar", "all"):
            idx = [i for it in items if lang in (it["language"], "all") for (i,) in it["gold"].values()]
            self.assertTrue(any(i >= 4 for i in idx) and any(i < 3 for i in idx))   # both sides of each cutoff occur
            for kind in KINDS:
                got = r["metrics"]["e5"][kind][lang]
                self.assertEqual(got["recall_at_1"], 0.0)   # line 0 is the model heading, never gold
                self.assertEqual(got["recall_at_3"], sum(i < 3 for i in idx) / len(idx))
                self.assertAlmostEqual(got["mrr_at_4"], sum(1 / (i + 1) for i in idx if i < 4) / len(idx), places=12)

    def test_lexical_ranking_is_the_engines_retrieve_over_every_line(self):
        items = hf.retrieval_set(10, seed=7); calls = []; real = s.retrieve
        def counted(claims, query):
            calls.append(([c["quote"] for c in claims], query)); return real(claims, query)
        with mock.patch.object(s, "retrieve", counted):
            r = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        want = [(it["lines"], q) for it in items for a in it["gold"] for q in _queries(a).values()]
        self.assertEqual(sorted(calls), sorted(want)); self.assertEqual(len(calls), 3 * r["queries"])
        for kind in KINDS:
            for lang in ("en", "ar", "all"):
                rr, hits = [], []
                for it in items:
                    if lang not in (it["language"], "all"): continue
                    rows = [{"quote": x, "page": 1, "source": ""} for x in it["lines"]]
                    for a, (i,) in it["gold"].items():
                        top = [x["quote"] for x in real(rows, _queries(a)[kind])]
                        rank = top.index(it["lines"][i]) + 1 if it["lines"][i] in top else None
                        rr.append(1 / rank if rank else 0.0); hits.append(rank)
                got = r["metrics"]["lexical"][kind][lang]
                self.assertEqual(got["recall_at_1"], sum(h == 1 for h in hits) / len(hits))
                self.assertEqual(got["recall_at_3"], sum(h is not None and h <= 3 for h in hits) / len(hits))
                self.assertAlmostEqual(got["mrr_at_4"], sum(rr) / len(rr), places=12)

    def test_one_embedding_call_per_document_and_one_for_the_queries(self):
        calls = []; oracle = _oracle_embedder()
        def recording(strings):
            calls.append(list(strings)); return oracle(strings)
        hf.sanad_hf(recording, n_docs=4, seed=7)
        items = hf.retrieval_set(4, seed=7)
        self.assertEqual(sorted(calls[0]), sorted("query: " + q for a in s.ATTR for q in _queries(a).values()))
        self.assertEqual(len(calls[0]), 15)
        self.assertEqual(calls[1:], [["passage: " + x for x in it["lines"]] for it in items])

    def test_refuses_malformed_embeddings(self):
        oracle = _oracle_embedder()
        for name, bad in (("one row short", lambda t: oracle(t)[:-1]), ("flat", lambda t: oracle(t).ravel()),
                          ("nan", lambda t: oracle(t) * np.nan), ("zero row", lambda t: oracle(t) * 0),
                          ("width differs", lambda t: np.hstack([oracle(t)] * (2 if t[0].startswith("passage: ") else 1)))):
            with self.subTest(name), self.assertRaises(Refused) as caught:
                hf.sanad_hf(bad, n_docs=2, seed=7)
            self.assertEqual(caught.exception.code, "EMBEDDING")

    def test_gate_passes_two_runs_whatever_the_embedder_and_names_every_check(self):
        first = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7); second = hf.sanad_hf(_constant_embedder, n_docs=10, seed=7)
        self.assertNotEqual(first["metrics"]["e5"], second["metrics"]["e5"])
        g = hf.sanad_gate(first, second)
        self.assertTrue(g["passed"]); self.assertEqual(g["mismatches"], [])
        self.assertEqual(g["checked"][:3], ["set_digest", "queries", "lexical_ranker"])
        self.assertEqual(len(g["checked"]), 3 + 27); self.assertEqual(len(set(g["checked"])), 30)
        self.assertIn("metrics.lexical.ar.en.mrr_at_4", g["checked"])
        self.assertFalse(any(".e5." in n for n in g["checked"]))

    def test_gate_fails_when_a_lexical_metric_differs(self):
        first = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        second = json.loads(json.dumps(first)); second["metrics"]["lexical"]["en"]["ar"]["recall_at_3"] += 1e-12
        g = hf.sanad_gate(first, second)
        self.assertFalse(g["passed"]); self.assertEqual(g["mismatches"], ["metrics.lexical.en.ar.recall_at_3"])
        del second["metrics"]["lexical"]["en"]["ar"]["recall_at_3"]   # absent on one side never matches
        self.assertEqual(hf.sanad_gate(first, second)["mismatches"], ["metrics.lexical.en.ar.recall_at_3"])
        self.assertFalse(hf.sanad_gate({}, {})["passed"])

    def test_gate_fails_on_another_set_or_another_lexical_ranker(self):
        first = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        g = hf.sanad_gate(first, hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=8))
        self.assertFalse(g["passed"]); self.assertIn("set_digest", g["mismatches"])
        real = s.retrieve
        def wrapped(claims, query):
            return real(claims, query)   # same numbers, but not the engine function
        with mock.patch.object(s, "retrieve", wrapped):
            other = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
        self.assertEqual(other["metrics"]["lexical"], first["metrics"]["lexical"])
        self.assertEqual(hf.sanad_gate(first, other)["mismatches"], ["lexical_ranker"])
        self.assertEqual(hf.sanad_gate(other, other)["mismatches"], ["lexical_ranker"])


PASS = {"passed": True, "checked": ["stubbed"], "mismatches": []}
FAIL = {"passed": False, "checked": ["detected", "false_alerts"], "mismatches": ["detected"]}
STUB_LOADERS = {"basira": lambda root: b.features, "rasid": lambda root: naive_stub, "sanad": lambda root: _oracle_embedder()}
# Reduced BASIRA and RASID sizes for the writing path; SANAD at the protocol size its write guard requires.
SMALL = {"basira": QUICK, "rasid": {"days": 2}, "sanad": {"n_docs": 120, "seed": 2024}}
SYSTEMS = ("basira", "rasid", "sanad")


def _identity(kind, root):
    pin = models.PINS[kind]
    return {"repo": pin["repo"], "revision": pin["revision"], "weight_sha256": pin["weight_sha256"],
            "manifest_sha256": "a" * 64, "manifest_object": "b" * 64}


class EvaluateScriptTests(unittest.TestCase):
    """scripts/evaluate_hf.py with stub models: gates before writing, atomic files, repeat check (no torch)."""

    def setUp(self):
        self.out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.err = self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(mock.patch.object(models, "model_identity", side_effect=_identity))

    def read(self, kind):
        return json.loads((self.out / f"{kind}.json").read_text())

    def test_failed_gate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(hf, "basira_gate", return_value={"passed": False, "checked": [], "mismatches": ["detected"]}):
            with self.assertRaises(SystemExit) as e:
                evaluate_hf.run("unused", d, only="basira", loaders={"basira": lambda root: b.features}, quick=True)
            self.assertEqual(e.exception.code, 2); self.assertEqual(list(Path(d).iterdir()), [])

    def test_a_failed_gate_stops_before_the_model_loads_and_names_the_mismatch(self):
        loaded = []; out = self.out / "reports" / "hf"
        with mock.patch.dict(evaluate_hf.FULL_SIZES, SMALL), mock.patch.object(hf, "basira_gate", return_value=FAIL), \
             self.assertRaises(SystemExit) as e:
            evaluate_hf.run("unused", out, only="basira", loaders={"basira": lambda root: loaded.append(root) or b.features})
        self.assertEqual(e.exception.code, 2); self.assertEqual(loaded, []); self.assertFalse(out.exists())
        self.assertIn("basira: gate failed, 1 of 2 checks differ: detected", self.err.getvalue())

    def test_nothing_is_written_until_every_requested_gate_passes(self):
        with mock.patch.dict(evaluate_hf.FULL_SIZES, SMALL), mock.patch.object(hf, "basira_gate", return_value=PASS), \
             mock.patch.object(hf, "rasid_gate", return_value={**FAIL, "mismatches": ["target_days"]}), self.assertRaises(SystemExit) as e:
            evaluate_hf.run("unused", self.out, loaders=STUB_LOADERS)   # BASIRA passes and is complete; RASID's gate fails
        self.assertEqual(e.exception.code, 2); self.assertEqual(list(self.out.iterdir()), [])
        self.assertIn("basira: gate passed", self.err.getvalue()); self.assertIn("rasid: gate failed", self.err.getvalue())

    def test_writes_every_record_once_all_gates_pass(self):
        renames = []; real = os.replace
        def renaming(src, dst):
            renames.append((Path(src).name, Path(dst).name, sorted(p.name for p in self.out.iterdir()))); return real(src, dst)
        with mock.patch.dict(evaluate_hf.FULL_SIZES, SMALL), mock.patch.object(hf, "basira_gate", return_value=PASS), \
             mock.patch.object(hf, "rasid_gate", return_value=PASS), mock.patch.object(evaluate_hf.os, "replace", side_effect=renaming):
            result = evaluate_hf.run("unused", self.out, loaders=STUB_LOADERS)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [k + ".json" for k in SYSTEMS])   # no staging file left
        # Each file is staged in the output directory and renamed into place; all are staged before the first rename.
        self.assertEqual([dst for _, dst, _ in renames], [k + ".json" for k in SYSTEMS])
        for (src, _, _), kind in zip(renames, SYSTEMS):
            self.assertTrue(src.startswith(f".{kind}.") and src.endswith(".tmp"), src)
        self.assertEqual(renames[0][2], sorted(src for src, _, _ in renames))
        lines = evaluate_hf.summary(result).splitlines()
        self.assertEqual([line.split(":")[0] for line in lines], list(SYSTEMS))
        self.assertTrue(all("gate passed" in line for line in lines))
        for kind in SYSTEMS:
            r = self.read(kind)
            self.assertEqual(r, json.loads(canonical(result[kind])))
            self.assertEqual(set(r), {"gate", "baseline", "model", "identity", "environment", "settings", "notes",
                                      "elapsed_seconds", "fixture_only"})
            self.assertTrue(r["gate"]["passed"]); self.assertTrue(r["gate"]["rule"]); self.assertTrue(r["gate"]["against"])
            self.assertEqual(r["identity"], {**_identity(kind, "unused"), "note": evaluate_hf.IDENTITY_NOTE})
            self.assertEqual({k: r["environment"][k] for k in evaluation.environment()}, evaluation.environment())
            for k in ("torch", "transformers", "chronos_forecasting", "torch_threads"):
                self.assertIn(k, r["environment"])
            self.assertTrue(r["notes"]); self.assertIs(r["fixture_only"], True)
        for text in ("manifest_sha256", "this machine's signed import", "manifest_object", "reproducible by anyone re-importing the pin"):
            self.assertIn(text, evaluate_hf.IDENTITY_NOTE)
        basira, rasid, sanad = (self.read(k) for k in SYSTEMS)
        # BASIRA: the gated baseline is the shipped descriptor; the model is the loaded feature function under its own profile.
        self.assertEqual(basira["baseline"]["profile"], b.DESCRIPTOR)
        self.assertEqual(basira["model"]["profile"], "hf:facebook/dinov2-small@" + models.PINS["basira"]["revision"] + "/224-imagenet-v1")
        self.assertEqual((basira["settings"]["draws"], basira["settings"]["sizes"]), (3, [24]))
        # RASID: one run split into the ridge view and the Chronos view; the method texts are in the settings.
        self.assertEqual(set(rasid["baseline"]["decision_quality"]), set(evaluation.POLICIES))
        self.assertEqual(set(rasid["model"]["decision_quality"]), {"chronos", "chronos_risk_neutral"})
        self.assertEqual(len(rasid["model"]["paired_comparisons"]), 3)
        self.assertEqual((rasid["settings"]["scenario_rule"], rasid["settings"]["forecast_note"]), (hf.SCENARIO_RULE, hf.FORECAST_NOTE))
        for text in ("sorted across levels", "monotone rearrangement", "clipped at zero"):
            self.assertIn(text, rasid["settings"]["quantile_repair"])
        self.assertEqual(set(rasid["gate"]["total_cost_relative_difference"]), set(evaluation.POLICIES))
        # SANAD: the protocol size, the labelled query count and the ceiling note.
        self.assertEqual((sanad["settings"]["n_docs"], sanad["settings"]["seed"]), (120, 2024))
        for half in ("baseline", "model"):
            self.assertEqual(sanad[half]["queries_unit"], "(document, attribute) pairs, each ranked with three query kinds")
        self.assertEqual(sanad["model"]["metrics"]["bilingual"]["all"]["recall_at_1"], 1.0)   # the oracle stub
        notes = " ".join(sanad["notes"])
        for text in ("near its ceiling", "exact attribute label", "English-only and Arabic-only queries are the discriminating comparison"):
            self.assertIn(text, notes)

    def test_repeat_records_an_identical_second_run(self):
        with mock.patch.dict(evaluate_hf.FULL_SIZES, SMALL), mock.patch.object(hf, "rasid_gate", return_value=PASS):
            r = evaluate_hf.run("unused", self.out, only="rasid", repeat=True, loaders=STUB_LOADERS)["rasid"]
        self.assertIs(r["repeat_run"]["identical"], True); self.assertEqual(r["repeat_run"]["differences"], [])
        self.assertIs(self.read("rasid")["repeat_run"]["identical"], True)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["rasid.json"])

    def test_repeat_keeps_the_first_result_and_names_every_difference(self):
        loads = iter([naive_stub, stub])   # the model reloads as another forecaster; the ridge side cannot change
        with mock.patch.dict(evaluate_hf.FULL_SIZES, SMALL), mock.patch.object(hf, "rasid_gate", return_value=PASS):
            r = evaluate_hf.run("unused", self.out, only="rasid", repeat=True, loaders={"rasid": lambda root: next(loads)})["rasid"]
        rr = r["repeat_run"]
        self.assertIs(rr["identical"], False); self.assertEqual(rr["differing_values"], len(rr["differences"]))
        self.assertTrue(rr["differences"]); self.assertTrue(all(d["path"].startswith("model.") for d in rr["differences"]), rr["differences"])
        mae = next(d for d in rr["differences"] if d["path"] == "model.forecast.chronos_mae_kw")
        self.assertEqual(mae["first"], r["model"]["forecast"]["chronos_mae_kw"])   # the first run is the one kept
        self.assertEqual(mae["abs_difference"], abs(mae["first"] - mae["second"])); self.assertGreater(mae["abs_difference"], 0)
        self.assertEqual(rr["max_abs_difference"], max(d.get("abs_difference", 0) for d in rr["differences"]))
        self.assertEqual(self.read("rasid")["repeat_run"], json.loads(canonical(rr)))

    def test_differences_cover_absent_values_and_list_lengths(self):
        d = evaluate_hf.differences({"a": [1, 2.5], "b": "x", "c": True}, {"a": [1, 2.0, 3], "b": "y", "d": None})
        self.assertEqual(d, [{"path": "a[1]", "first": 2.5, "second": 2.0, "abs_difference": 0.5},
                             {"path": "a[2]", "first": None, "second": 3, "absent_in": "first"},
                             {"path": "b", "first": "x", "second": "y"},
                             {"path": "c", "first": True, "second": None, "absent_in": "second"},
                             {"path": "d", "first": None, "second": None, "absent_in": "first"}])
        self.assertEqual(evaluate_hf.differences({"x": [1.0, {"y": 2}]}, {"x": [1.0, {"y": 2}]}), [])
        self.assertEqual(evaluate_hf.differences({"x": 1}, {"x": 1.0}), [{"path": "x", "first": 1, "second": 1.0, "abs_difference": 0.0}])

    def test_sanad_results_are_written_only_at_the_protocol_size(self):
        for size in ({"n_docs": 10, "seed": 2024}, {"n_docs": 120, "seed": 2025}):
            with self.subTest(**size), mock.patch.dict(evaluate_hf.FULL_SIZES, {"sanad": size}), self.assertRaises(Refused) as caught:
                evaluate_hf.run("unused", self.out, only="sanad", loaders=STUB_LOADERS)   # its gate passes at any size
            self.assertEqual(caught.exception.code, "PROTOCOL_SIZE"); self.assertEqual(list(self.out.iterdir()), [])

    def test_quick_mode_runs_every_system_at_reduced_size_and_never_writes(self):
        out = self.out / "never"
        with mock.patch.object(hf, "basira_gate", return_value=PASS), mock.patch.object(hf, "rasid_gate", return_value=PASS):
            r = evaluate_hf.run("unused", out, repeat=True, loaders=STUB_LOADERS, quick=True)
        self.assertFalse(out.exists()); self.assertEqual(set(r), set(SYSTEMS)); self.assertEqual(self.err.getvalue(), "")
        self.assertEqual(r["basira"]["model"]["calibration_study"]["draws_per_size"], 3)
        self.assertEqual(r["basira"]["model"]["calibration_study"]["pools"],
                         {"calibration_pool_normals": 60, "evaluation_normals": 60, "evaluation_defects": 20})
        self.assertEqual(r["rasid"]["baseline"]["target_days"], 2); self.assertEqual(r["sanad"]["settings"]["n_docs"], 10)
        self.assertTrue(all(r[k]["repeat_run"]["identical"] for k in SYSTEMS))

    def test_rasid_views_split_one_run_without_losing_a_field(self):
        r = hf.rasid_hf(stub, days=2); base, model = evaluate_hf.rasid_views(r)
        for i, row in enumerate(r["origins"]):
            self.assertEqual({**base["origins"][i], **model["origins"][i]}, row)
            self.assertEqual(base["origins"][i]["date"], model["origins"][i]["date"])
            self.assertFalse({"ridge_mae", "candidate_cost"} & set(model["origins"][i]))
            self.assertFalse({"chronos_mae", "chronos_cost"} & set(base["origins"][i]))
        self.assertEqual({**base["forecast"], **model["forecast"], "note": r["forecast"]["note"]}, r["forecast"])
        self.assertEqual({**base["decision_quality"], **model["decision_quality"]}, r["decision_quality"])
        self.assertEqual(set(base["decision_quality"]), set(evaluation.POLICIES))
        self.assertEqual(model["paired_comparisons"], r["paired_comparisons"]); self.assertNotIn("paired_comparisons", base)
        self.assertEqual(hf.rasid_gate(base, RASID), hf.rasid_gate(r, RASID))   # the baseline holds everything the gate reads
        r["origins"][1]["extra"] = 1.0
        with self.assertRaises(Refused) as caught:
            evaluate_hf.rasid_views(r)
        self.assertEqual(caught.exception.code, "SPLIT")

    def test_sanad_views_hold_the_lexical_and_e5_halves_of_one_run(self):
        r = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7); base, model = evaluate_hf.sanad_views(r)
        self.assertEqual((base["retriever"], base["ranker"], base["metrics"]), ("lexical", hf.LEXICAL_RANKER, r["metrics"]["lexical"]))
        self.assertEqual((model["retriever"], model["metrics"]), ("e5", r["metrics"]["e5"]))
        for k in ("set_digest", "documents", "queries", "queries_by_language"):
            self.assertEqual((base[k], model[k]), (r[k], r[k]))
        self.assertEqual(base["queries_unit"], model["queries_unit"])
        with self.assertRaises(Refused) as caught:
            evaluate_hf.sanad_views({**r, "extra": 1})
        self.assertEqual(caught.exception.code, "SPLIT")


if __name__ == "__main__":
    unittest.main()
