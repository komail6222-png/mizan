"""Derive paired 95% intervals for the RASID accuracy comparisons, without running any model (torch-free).

    python scripts/derive_hf_intervals.py        # writes reports/hf/rasid_intervals.json

The per-origin forecast errors and daily costs are already in reports/hf/rasid.json (Chronos-Bolt-tiny and the ridge
forecaster, from one run) and reports/evaluation/rasid.json (the shipped ridge and seasonal-naive errors that the 3.0
figures use). This script pairs them day by day and applies the project's own bootstrap, mizan.evaluation.paired_comparison,
with its shipped settings (7-day moving blocks, 4,000 draws, seed 2026): the method of every paired cost interval in the
reports. The evidence files are read, never changed. The output names each input's SHA-256, the bootstrap settings and
each interval's unit and orientation, and also records the risk-weighted planner settings its reading depends on.

tests/test_hf_intervals.py recomputes the file from its inputs and requires byte equality, so after a new evidence run
(scripts/evaluate_hf.py) this script must be run again.
"""
import argparse
import hashlib
import inspect
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import evaluation, examples  # noqa: E402  (evaluation.paired_comparison is looked up at call time)
from mizan.core.contract import canonical, require  # noqa: E402

HF = "reports/hf/rasid.json"
SHIPPED = "reports/evaluation/rasid.json"
OUT = "reports/hf/rasid_intervals.json"

# name, input file, side a (view, per-origin field), side b, unit, orientation. view None: the file's own "origins".
COMPARISONS = (
    ("chronos_minus_ridge_load_mae", HF, ("model", "chronos_mae"), ("baseline", "ridge_mae"), "kW (mean absolute error of the load forecast over the 24 hours)",
     "Chronos minus ridge; negative means Chronos had the lower error"),
    ("chronos_minus_ridge_mean_pinball", HF, ("model", "chronos_mean_pinball"), ("baseline", "ridge_mean_pinball"),
     "kW (mean pinball loss of the load quantiles at the shared levels 0.1, 0.5 and 0.9)", "Chronos minus ridge; negative means Chronos had the lower loss"),
    ("chronos_minus_ridge_truncated_crps", HF, ("model", "chronos_truncated_crps"), ("baseline", "ridge_truncated_crps"),
     "kW (truncated CRPS of the load quantiles at the shared levels 0.1, 0.5 and 0.9)", "Chronos minus ridge; negative means Chronos had the lower CRPS"),
    ("chronos_minus_ridge_coverage_80_load", HF, ("model", "chronos_coverage_80_load"), ("baseline", "coverage_80_load"),
     "share of the 24 load hours inside the 0.1 to 0.9 range (nominal 0.8)", "Chronos minus ridge; negative means Chronos covered fewer hours"),
    ("ridge_minus_seasonal_naive_load_mae", SHIPPED, (None, "ridge_mae"), (None, "naive_mae"), "kW (mean absolute error of the load forecast over the 24 hours)",
     "ridge minus seasonal naive; negative means the ridge forecast had the lower error"),
    ("chronos_minus_chronos_risk_neutral_cost", HF, ("model", "chronos_cost"), ("model", "chronos_risk_neutral_cost"), "illustrative cost units per day",
     "Chronos risk-weighted minus Chronos risk-neutral: the price of risk aversion on the Chronos scenarios"),
    ("candidate_minus_candidate_risk_neutral_cost", HF, ("baseline", "candidate_cost"), ("baseline", "candidate_risk_neutral_cost"), "illustrative cost units per day",
     "ridge risk-weighted minus ridge risk-neutral: the price of risk aversion on the ridge scenarios"),
)


def _sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _origins(data, view):
    return data["origins"] if view is None else data[view]["origins"]


def _bootstrap_settings(fn):
    """The defaults of mizan.evaluation.paired_comparison: the settings every shipped paired interval uses."""
    p = inspect.signature(fn).parameters
    return {k: p[k].default for k in ("block", "draws", "seed")}


BOOTSTRAP = _bootstrap_settings(evaluation.paired_comparison)


def derive(root=ROOT):
    files = {p: json.loads((Path(root) / p).read_bytes()) for p in (HF, SHIPPED)}
    settings = BOOTSTRAP
    hf = files[HF]
    require([o["date"] for o in hf["baseline"]["origins"]] == [o["date"] for o in hf["model"]["origins"]], "PAIRING",
            "The ridge and Chronos views no longer cover the same origins")
    out, method = [], None
    for name, path, (va, fa), (vb, fb), unit, orientation in COMPARISONS:
        a, b = _origins(files[path], va), _origins(files[path], vb)
        require([o["date"] for o in a] == [o["date"] for o in b], "PAIRING", f"{name}: the two series cover different days")
        rows = [{"a_cost": x[fa], "b_cost": y[fb]} for x, y in zip(a, b)]
        c = evaluation.paired_comparison(rows, "a", "b", **settings)
        require(method in (None, c["bootstrap"]), "BOOTSTRAP", "The comparisons used different bootstrap settings")
        method = c["bootstrap"]
        lo, hi = c["mean_daily_difference_95ci"]
        out.append({"comparison": name, "file": path, "a": f"{va + '.' if va else ''}origins[].{fa}", "b": f"{vb + '.' if vb else ''}origins[].{fb}",
                    "unit": unit, "orientation": orientation, "days": len(rows),
                    "mean_a": float(np.mean([r["a_cost"] for r in rows])), "mean_b": float(np.mean([r["b_cost"] for r in rows])),
                    "mean_difference": c["mean_daily_difference"], "interval_95": [lo, hi],
                    "days_a_below_b": c["days_cheaper"], "days_a_above_b": c["days_more_expensive"], "resolved": bool(lo > 0 or hi < 0)})
    shipped = examples.settings(24)
    weights = hf["settings"]["scenario_weights"]
    return {
        "source": {"files": {p: _sha256(p) for p in (HF, SHIPPED)},
                   "bootstrap": {"function": "mizan.evaluation.paired_comparison", "block_days": settings["block"], "draws": settings["draws"],
                                 "seed": settings["seed"], "method": method,
                                 "interval": "2.5% and 97.5% quantiles of the resampled mean daily difference (a minus b)"},
                   "derived_by": "scripts/derive_hf_intervals.py",
                   "note": ("Derived from the per-origin values already in the evidence files; no model was run and the evidence "
                            "files are unchanged. Days are dependent through carried battery state and the monthly peak, so whole "
                            "weeks are resampled, as for the paired cost intervals. 'resolved' means the 95% interval excludes zero.")},
        "comparisons": out,
        "risk_weighted_planner": {
            "risk_weight": shipped["risk"], "cvar_beta": shipped["beta"],
            "settings_from": "mizan.examples.settings(24), which mizan.evaluation.policy_problem uses for the candidate and chronos policies",
            "objective": "(1 - risk_weight) x expected scenario cost + risk_weight x CVaR of scenario cost at cvar_beta (mizan.rasid.solver.solve)",
            "chronos_scenario_weights": weights,
            "chronos_cvar_is_costliest_scenario": bool(min(weights) >= 1 - shipped["beta"]),
            "rule": ("When every scenario weighs at least 1 - cvar_beta, the worst 1 - cvar_beta of the scenario distribution lies "
                     "inside the single costliest scenario, so the CVaR term equals that scenario's cost."),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / OUT))
    args = parser.parse_args(argv)
    data = canonical(derive())
    out = Path(args.out)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.fchmod(fd, 0o644)   # readable by all, whatever the umask
    with open(fd, "wb") as f:
        f.write(data)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
