"""RASID on real load (OPSD): within-run statistics, the day loop, the generated-site gate and the evidence record.

Days are dependent through the battery charge and the month-to-date peak each policy carries, so intervals come from
a moving-block bootstrap whose blocks never span a gap between runs of target days (spec rule 10). The Chronos
forecaster is injected, so this module and its tests stay torch-free.
"""
import copy
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

import numpy as np

from .core.contract import Refused, require
from .evaluation import realized
from .hf_evaluation import (CHRONOS_LEVELS, PLANNED_AS, POLICIES_HF, SHARED_LEVELS, _columns, _compare, _decision_summary,
                            _MISSING, _within, chronos_context, rasid_gate)
from .opsd import PROTOCOL
from .rasid.forecast import forecast, quantile_scores, records
from .rasid.input import checked
from .rasid.solver import solve
from .rasid.verify import verify
from .visa_evaluation import reading

HOUR = timedelta(hours=1)
ROOT = Path(__file__).resolve().parents[1]
CHRONOS_FORECAST_KEYS = ("chronos_mae_kw", "chronos_mean_pinball", "chronos_truncated_crps", "chronos_coverage_80_load")
CHRONOS_MAE_TOLERANCE = 1e-9      # absolute, as for the ridge errors
CHRONOS_COST_TOLERANCE = 1e-6     # relative: the chronos and chronos_risk_neutral totals

BLOCK = PROTOCOL["values"]["block_days"]
DRAWS = PROTOCOL["values"]["draws"]
SEED = PROTOCOL["values"]["seed"]


def run_blocks(run_lengths, block=BLOCK) -> list:
    """(start, length) of every allowed block in the concatenated day order: each window of `block` consecutive days
    inside one run, or the whole run when it is shorter than `block`."""
    out, start = [], 0
    for n in run_lengths:
        if n < block:
            out.append((start, n))
        else:
            out.extend((start + k, block) for k in range(n - block + 1))
        start += n
    return out


def block_draws(n, run_lengths, *, draws=DRAWS, seed=SEED, block=BLOCK) -> np.ndarray:
    """Resampled day indices, shape (draws, n): blocks chosen uniformly from run_blocks(), concatenated, truncated."""
    require(n > 0 and sum(run_lengths) == n, "OPSD_NO_DAYS", "No target days to resample")
    blocks = run_blocks(run_lengths, block)
    rng = np.random.default_rng(seed)
    out = np.empty((draws, n), dtype=np.int64)
    for d in range(draws):
        idx = []
        while len(idx) < n:
            start, length = blocks[int(rng.integers(len(blocks)))]
            idx.extend(range(start, start + length))
        out[d] = idx[:n]
    return out


def _interval(values):
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    lo, hi = np.quantile(values, [.025, .975])
    return [float(lo), float(hi)]


def _describe(draws, seed, block=BLOCK):
    return f"within-run blocks of {block} days, {draws} draws, seed {seed}"


def paired(diffs, run_lengths, *, draws=DRAWS, seed=SEED, idx=None) -> dict:
    """Mean and total of per-day differences, with a within-run block-bootstrap 95% interval and its reading."""
    diffs = np.asarray(diffs, dtype=np.float64)
    require(diffs.size > 0, "OPSD_NO_DAYS", "No target days to compare")
    idx = block_draws(diffs.size, run_lengths, draws=draws, seed=seed) if idx is None else idx
    lo, hi = _interval(diffs[idx].mean(axis=1))
    return {"mean": float(diffs.mean()), "total": float(diffs.sum()), "ci95": [lo, hi], "reading": reading(lo, hi),
            "bootstrap": _describe(len(idx), seed)}


def _costs(rows, policy):
    return np.array([[r["no_battery_cost"], r[policy + "_cost"], r["perfect_foresight_cost"]] for r in rows], dtype=np.float64)


def _share(costs):
    """(no battery - policy) / (no battery - hindsight) over the summed days; None when nothing is achievable."""
    nb, pol, pf = costs.sum(axis=0)
    return None if nb - pf <= 0 else float((nb - pol) / (nb - pf))


def _share_draws(costs, idx):
    sums = costs[idx].sum(axis=1)          # (draws, 3)
    gain = sums[:, 0] - sums[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(gain > 0, (sums[:, 0] - sums[:, 1]) / gain, np.nan)


def share_of_value(rows, policy):
    """The share of the battery's achievable value a policy captures, over the given days."""
    return _share(_costs(rows, policy))


def share_interval(rows, policy, run_lengths, *, draws=DRAWS, seed=SEED, idx=None) -> dict:
    costs = _costs(rows, policy)
    require(len(costs) > 0, "OPSD_NO_DAYS", "No target days")
    idx = block_draws(len(costs), run_lengths, draws=draws, seed=seed) if idx is None else idx
    return {"value": _share(costs), "ci95": _interval(_share_draws(costs, idx)), "bootstrap": _describe(len(idx), seed)}


def pooled(per_site: dict, *, draws=DRAWS, seed=SEED, point=None, stat=None) -> dict:
    """The equal-weight mean over sites of a unit-free statistic. Each site is resampled independently (seed + its
    index in sorted site order). By default the statistic is the mean of the site's per-day values; `point(values)`
    and `stat(values, idx)` replace it (for a share, values are the per-day cost columns)."""
    point = point or (lambda v: float(np.mean(v)))
    stat = stat or (lambda v, idx: v[idx].mean(axis=1))
    points, curves = [], []
    for i, site in enumerate(sorted(per_site)):
        values, runs = per_site[site]
        values = np.asarray(values, dtype=np.float64)
        require(len(values) > 0, "OPSD_NO_DAYS", f"{site} has no target days")
        idx = block_draws(len(values), runs, draws=draws, seed=seed + i)
        points.append(point(values))
        curves.append(stat(values, idx))
    lo, hi = _interval(np.mean(curves, axis=0))
    return {"mean": float(np.mean(points)), "ci95": [lo, hi], "reading": reading(lo, hi),
            "sites": sorted(per_site), "weights": "equal per site", "bootstrap": _describe(draws, seed)}


# ---------------------------------------------------------------------------------------------------------------
# The day loop (spec rules 5, 6, 8 and 12) and the generated-site gate

def day_problem(name, f, y, state, peak, base: dict) -> dict:
    """evaluation.policy_problem with the site's settings in place of examples.settings(24): the month-to-date peak
    as the established peak, the carried charge as the initial charge, the base terminal charge."""
    p = copy.deepcopy(base)
    p["tariff"]["established"] = peak
    p["assets"]["initial"] = state
    p["assets"]["terminal"] = base["assets"]["terminal"]
    weights, paths = {"candidate": (f["weights"], f["scenarios"]), "candidate_risk_neutral": (f["weights"], f["scenarios"]),
                      "seasonal_naive": ([1], [f["seasonal_naive"]]), "perfect_foresight": ([1], [y.tolist()]),
                      "no_battery": ([1], [f["seasonal_naive"]])}[name]
    p.update(hours=[1] * 24, weights=weights, scenarios=paths)
    if name == "candidate_risk_neutral":
        p["risk"] = 0
    if name == "no_battery":
        p["assets"].update(capacity=0, power=0, initial=0, terminal=0)
    return checked(p)


def _utc_date(origin):
    return origin.date().isoformat()


def _chronos_scenarios(all_rows, origin, forecaster):
    """The Chronos context, forecast and SCENARIO_RULE inputs: sorted quantiles with solar clipped at zero."""
    load, pv, n, last = chronos_context(all_rows, origin)
    require(last == origin - HOUR, "CONTEXT", "The latest known hour must be the hour before the origin")
    c = np.asarray(forecaster(load, pv, CHRONOS_LEVELS), dtype=np.float64)
    require(c.shape == (24, 2, len(CHRONOS_LEVELS)) and np.all(np.isfinite(c)), "FORECAST",
            "Expected finite (24, 2, levels) quantiles")
    c = np.sort(c, axis=-1)
    clipped = int(np.sum(c[:, 1, :] < 0))
    c[:, 1, :] = np.maximum(c[:, 1, :], 0)
    return c, n, clipped


def plan_days(origins, rows_at, actual, base: dict, forecaster, *, grid_at, month_of, date_of=_utc_date) -> dict:
    """hf_evaluation.rasid_hf() operation for operation, over runs of origins.

    Each policy carries its charge and month-to-date peak; the peak resets when month_of(origin) changes and both reset
    at the start of every run and after every dropped day (rule 8). A ridge refusal is counted by cause (rule 5). A
    refused Chronos context or forecast, a refused planner input, no plan or a rejected plan drops the day for every
    policy and is recorded by date, policy and cause (rule 12). Each kept row also records each policy's starting
    charge and peak and its run."""
    out, run_lengths, ridge_refused, planning_refused = [], [], Counter(), []
    lo, mid, hi = _columns(CHRONOS_LEVELS, SHARED_LEVELS)
    top = len(CHRONOS_LEVELS) - 1
    for run in origins:
        fresh = True
        for origin in run:
            if fresh:
                peaks = {k: 0. for k in POLICIES_HF}
                states = {k: (0. if k == "no_battery" else float(base["assets"]["initial"])) for k in POLICIES_HF}
                last_month = month_of(origin)
            if month_of(origin) != last_month:
                peaks = {k: 0. for k in POLICIES_HF}
                last_month = month_of(origin)
            grid = grid_at(origin)
            try:
                all_rows = records(rows_at(origin))
                f = forecast(all_rows, origin, grid, 4)
            except Refused as exc:
                ridge_refused[exc.code] += 1
                fresh = True
                continue
            y = np.array([actual(t) for t in grid])
            q = np.array(f["quantiles"])[:, 0, :]
            row = {"date": date_of(origin), "ridge_mae": float(np.mean(abs(np.array(f["point"])[:, 0] - y[:, 0]))),
                   "naive_mae": float(np.mean(abs(np.array(f["seasonal_naive"])[:, 0] - y[:, 0]))),
                   "coverage_80_load": float(np.mean((y[:, 0] >= q[:, 1]) & (y[:, 0] <= q[:, -2])))}
            ridge = quantile_scores(SHARED_LEVELS, q[:, _columns(f["levels"], SHARED_LEVELS)], y[:, 0])
            refusals, results = [], {}
            try:
                c, n, clipped = _chronos_scenarios(all_rows, origin, forecaster)
            except Refused as exc:
                refusals.append({"date": row["date"], "policy": "chronos", "cause": exc.code})
            if not refusals:
                cl = c[:, 0, :]
                chronos = quantile_scores(SHARED_LEVELS, cl[:, [lo, mid, hi]], y[:, 0])
                row.update(ridge_mean_pinball=ridge["mean_pinball"], ridge_truncated_crps=ridge["truncated_crps"],
                           chronos_mae=float(np.mean(abs(cl[:, mid] - y[:, 0]))),
                           chronos_coverage_80_load=float(np.mean((y[:, 0] >= cl[:, lo]) & (y[:, 0] <= cl[:, hi]))),
                           chronos_mean_pinball=chronos["mean_pinball"], chronos_truncated_crps=chronos["truncated_crps"],
                           context_hours=n, pv_values_clipped=clipped)
                fc = {**f, "weights": [1 / len(CHRONOS_LEVELS)] * len(CHRONOS_LEVELS),
                      "scenarios": [np.stack([c[:, 0, j], c[:, 1, top - j]], axis=1).tolist() for j in range(top + 1)]}
                for label in POLICIES_HF:
                    try:
                        p = day_problem(PLANNED_AS.get(label, label), fc if label in PLANNED_AS else f, y,
                                        states[label], peaks[label], base)
                        plan = solve(p)
                        cert = verify(p, plan)
                        bill, peak = realized(plan, y, p)
                        results[label] = (bill, peak, plan["soc"][-1], cert["physical_status"])
                    except Refused as exc:
                        refusals.append({"date": row["date"], "policy": label, "cause": exc.code})
            if refusals:
                planning_refused.extend(refusals)
                fresh = True
                continue
            if fresh:
                run_lengths.append(0)
                fresh = False
            for label, (bill, peak, soc, status) in results.items():
                row[label + "_start_charge"] = states[label]
                row[label + "_start_peak"] = peaks[label]
                peaks[label] = max(peaks[label], peak)
                states[label] = soc
                row[label + "_cost"] = bill
                row[label + "_status"] = status
            row["run"] = len(run_lengths) - 1
            run_lengths[-1] += 1
            out.append(row)
    return {"rows": out, "run_lengths": run_lengths, "ridge_refused": dict(ridge_refused), "planning_refused": planning_refused}


def summarize(rows) -> dict:
    """The forecast means and decision quality of rasid_hf(), over the given rows."""
    mean = lambda k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    return {"target_days": len(rows), "origins": rows,
            "forecast": {"ridge_mae_kw": mean("ridge_mae"), "seasonal_naive_mae_kw": mean("naive_mae"),
                         "chronos_mae_kw": mean("chronos_mae"), "shared_levels": list(SHARED_LEVELS),
                         "ridge_mean_pinball": mean("ridge_mean_pinball"), "ridge_truncated_crps": mean("ridge_truncated_crps"),
                         "ridge_coverage_80_load": mean("coverage_80_load"), "chronos_mean_pinball": mean("chronos_mean_pinball"),
                         "chronos_truncated_crps": mean("chronos_truncated_crps"),
                         "chronos_coverage_80_load": mean("chronos_coverage_80_load"),
                         "pv_values_clipped": sum(r["pv_values_clipped"] for r in rows)},
            "decision_quality": _decision_summary(rows, POLICIES_HF)}


def gate_check(result: dict, shipped_ridge: dict, shipped_hf: dict) -> dict:
    """The generated-site gate: the ridge side against reports/evaluation/rasid.json (hf_evaluation.rasid_gate), the
    Chronos side against the model side of reports/hf/rasid.json."""
    ridge = rasid_gate(result, shipped_ridge)
    got, want = result.get("forecast", {}), shipped_hf["model"]["forecast"]
    mae = _within(abs_tol=CHRONOS_MAE_TOLERANCE)
    pairs = [("forecast." + k, got.get(k, _MISSING), want.get(k, _MISSING), mae) for k in CHRONOS_FORECAST_KEYS]
    for k in ("chronos", "chronos_risk_neutral"):
        g, w = result.get("decision_quality", {}).get(k, {}), shipped_hf["model"]["decision_quality"][k]
        pairs += [(f"decision_quality.{k}.total_cost", g.get("total_cost", _MISSING), w.get("total_cost", _MISSING),
                   _within(rel_tol=CHRONOS_COST_TOLERANCE)),
                  (f"decision_quality.{k}.verified_feasible_plans", g.get("verified_feasible_plans", _MISSING),
                   w.get("verified_feasible_plans", _MISSING))]
    chronos = _compare(pairs)
    return {"passed": ridge["passed"] and chronos["passed"], "ridge": ridge, "chronos": chronos}


def generated_gate(forecaster) -> dict:
    """Run the day loop on the generated site exactly as rasid_hf() does (the shipped days of UTC origins from
    2026-02-12, one run, examples.settings(24)) and check it against the shipped evidence."""
    from datetime import datetime, timezone
    from . import examples
    shipped_ridge = json.loads((ROOT / "reports/evaluation/rasid.json").read_text(encoding="utf-8"))
    shipped_hf = json.loads((ROOT / "reports/hf/rasid.json").read_text(encoding="utf-8"))
    raw = examples.electricity(102)
    actual = {t: (l, p) for t, k, l, p in records(raw)}
    origin0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    origins = [[origin0 + timedelta(days=42 + i) for i in range(shipped_ridge["target_days"])]]
    planned = plan_days(origins, lambda o: raw, lambda t: actual[t], examples.settings(24), forecaster,
                        grid_at=lambda o: [o + timedelta(hours=h) for h in range(24)], month_of=lambda o: (o.year, o.month))
    require(not planned["ridge_refused"] and not planned["planning_refused"], "GATE", "The generated site must plan every day")
    return gate_check(summarize(planned["rows"]), shipped_ridge, shipped_hf)
