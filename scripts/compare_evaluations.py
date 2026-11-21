"""Compare two evaluation directories (for example the bundled reports and a rerun on another machine).

Exact equality is expected for SANAD rows and for BASIRA bank digests and p-values. RASID forecasts should match;
realized costs can differ when a different HiGHS build returns another equally certified optimum, because battery
state and the month-to-date peak carry into the next day.
"""
import argparse
import json
import platform
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load(folder):
    return {k: json.loads((Path(folder) / f"{k}.json").read_bytes()) for k in ("sanad", "basira", "rasid")}


def compare(reference, candidate):
    a, b = load(reference), load(candidate)
    days = list(zip(a["rasid"]["origins"], b["rasid"]["origins"]))
    differing = [x["date"] for x, y in days if abs(x["candidate_cost"] - y["candidate_cost"]) > 1e-6]
    ext = b["sanad"].get("grammar_2_extension")
    return {
        "sanad": {"original_rows_identical": a["sanad"]["rows"] == b["sanad"]["rows"],
                  "correct": [a["sanad"]["correct"], b["sanad"]["correct"]],
                  "false_meets": [a["sanad"]["false_meets"], b["sanad"]["false_meets"]],
                  "grammar_2_extension": None if ext is None else {k: ext[k] for k in ("fixture_rows", "correct", "false_meets", "decisive")}},
        "basira": {"bank_digest_identical": a["basira"]["bank_digest"] == b["basira"]["bank_digest"],
                   "p_values_identical": [r["p_value"] for r in a["basira"]["rows"]] == [r["p_value"] for r in b["basira"]["rows"]],
                   "detected": [a["basira"]["detected"], b["basira"]["detected"]],
                   "false_alerts": [a["basira"]["false_alerts"], b["basira"]["false_alerts"]]},
        "rasid": {"forecast_mae_identical_every_day": all(x["ridge_mae"] == y["ridge_mae"] and x["naive_mae"] == y["naive_mae"] for x, y in days),
                  "ridge_mae_kw": [a["rasid"]["ridge_mae_kw"], b["rasid"]["ridge_mae_kw"]],
                  "candidate_total_cost": [a["rasid"]["candidate_total_cost"], b["rasid"]["candidate_total_cost"]],
                  "seasonal_naive_total_cost": [a["rasid"]["seasonal_naive_total_cost"], b["rasid"]["seasonal_naive_total_cost"]],
                  "candidate_minus_baseline": [a["rasid"]["candidate_total_cost"] - a["rasid"]["seasonal_naive_total_cost"],
                                               b["rasid"]["candidate_total_cost"] - b["rasid"]["seasonal_naive_total_cost"]],
                  "verified_feasible_plans": [a["rasid"]["verified_feasible_plans"], b["rasid"]["verified_feasible_plans"]],
                  "days_with_different_realized_cost": len(differing),
                  "first_divergent_day": differing[0] if differing else None},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("candidate")
    parser.add_argument("--out")
    args = parser.parse_args()
    import numpy, scipy, sqlite3
    report = {"candidate_environment": {"python": platform.python_version(), "numpy": numpy.__version__,
                                        "scipy": scipy.__version__, "sqlite": sqlite3.sqlite_version, "platform": platform.platform()},
              "reference": str(args.reference), "candidate": str(args.candidate), "comparison": compare(args.reference, args.candidate)}
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
