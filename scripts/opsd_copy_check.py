"""A separate check on the OPSD evaluation: clean days that repeat, hour by hour, the day one or two weeks earlier.

    .venv/bin/python scripts/opsd_copy_check.py          # writes reports/opsd/copies.json

The sealed protocol's clean-day rule (mizan.opsd, rule 3) drops a day that copies the day before. The package also
fills gaps with data from earlier days without always flagging them, and some clean days repeat the day 7 or 14 days
earlier exactly. This script finds those days in the pinned data file and recomputes the published measures without
them from the stored rows of reports/opsd/rasid.json: the plans and costs are those of the sealed run, the removed
days leave the averages, and runs are split where a day is removed. It is not a re-planning, and it is not the sealed
analysis: it is a check of how much those days move the published results.
"""
import argparse
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import opsd  # noqa: E402
from mizan import opsd_evaluation as oe  # noqa: E402
from mizan.core.contract import require  # noqa: E402

LAGS = (7, 14)
NOTE = ("A separate check, not the sealed analysis: clean days whose grid import repeats, hour by hour, the day 7 or 14 "
        "days earlier are removed from the averages of the sealed run's stored rows; plans and costs are as run, runs are "
        "split where a day is removed, and the normalisers are the published ones.")


def week_copies(hours, clean, lags=LAGS) -> dict:
    """Clean local date -> the smallest lag (days) whose day has the same grid import, hour by hour (rounded as rule 3)."""
    out = {}
    for day in sorted(clean):
        imports = [round(hours[t][0], opsd.ROUNDING) for t in clean[day]]
        for lag in lags:
            earlier = opsd.local_day_hours(day - timedelta(days=lag))
            if len(earlier) == len(imports) and all(t in hours for t in earlier) and \
                    [round(hours[t][0], opsd.ROUNDING) for t in earlier] == imports:
                out[day] = lag
                break
    return out


def split_runs(rows, removed):
    """The rows left after removing the given dates, and their run lengths: a removed day splits its run."""
    kept, lengths, last = [], [], None
    for i, row in enumerate(rows):
        if row["date"] in removed:
            continue
        if last is None or row["run"] != rows[last]["run"] or i != last + 1:
            lengths.append(0)
        lengths[-1] += 1
        kept.append(row)
        last = i
    return kept, lengths


def sensitivity(record, removed_by_site, *, draws=oe.DRAWS) -> dict:
    sites, idx_by_site, per = {}, {}, {}
    norm = record["pooled"]["normalisers"]
    for i, site in enumerate(sorted(record["sites"])):
        rows = record["sites"][site]["rows"]
        removed = set(removed_by_site.get(site, ()))
        kept, runs = split_runs(rows, removed)
        require(kept, "OPSD_NO_DAYS", f"{site}: no day left")
        idx = oe.block_draws(len(kept), runs, draws=draws, seed=oe.SEED + i)
        idx_by_site[site] = idx
        per[site] = (kept, runs)
        load = norm[site]["mean_load_kw"]
        mean = lambda k: float(np.mean([r[k] for r in kept]))  # noqa: E731
        sites[site] = {
            "removed_target_days": len(rows) - len(kept), "target_days": len(kept), "runs": len(runs),
            "forecast": {"ridge_mae_kw": mean("ridge_mae"), "seasonal_naive_mae_kw": mean("naive_mae"),
                         "chronos_mae_kw": mean("chronos_mae"), "ridge_mae_share_of_load": mean("ridge_mae") / load,
                         "seasonal_naive_mae_share_of_load": mean("naive_mae") / load,
                         "chronos_mae_share_of_load": mean("chronos_mae") / load},
            "accuracy": [{"a": a, "b": b, **oe.paired([r[a] - r[b] for r in kept], runs, draws=draws, seed=oe.SEED + i, idx=idx)}
                         for a, b in oe.ACCURACY],
            "paired": [{"a": a, "b": b, **oe.paired([r[a + "_cost"] - r[b + "_cost"] for r in kept], runs, draws=draws,
                                                    seed=oe.SEED + i, idx=idx)} for a, b in oe.COMPARISONS],
            "share": {k: oe.share_interval(kept, k, runs, draws=draws, seed=oe.SEED + i, idx=idx) for k in oe.POLICIES_HF},
        }
    common = {"draws": draws, "idx_by_site": idx_by_site}
    pooled = {
        "share": {k: oe.pooled({s: (oe._costs(kept, k), runs) for s, (kept, runs) in per.items()},
                               point=oe._share, stat=oe._share_draws, **common) for k in oe.POLICIES_HF},
        "paired": [{"a": a, "b": b, **oe.pooled({s: (np.array([r[a + "_cost"] - r[b + "_cost"] for r in kept])
                                                     / norm[s]["mean_daily_no_battery_cost"], runs) for s, (kept, runs) in per.items()}, **common)}
                   for a, b in oe.COMPARISONS],
        "accuracy": [{"a": a, "b": b, **oe.pooled({s: (np.array([r[a] - r[b] for r in kept]) / norm[s]["mean_load_kw"], runs)
                                                   for s, (kept, runs) in per.items()}, **common)} for a, b in oe.ACCURACY],
    }
    return {"note": NOTE, "sites": sites, "pooled": pooled}


def check(data, record_path) -> dict:
    record_bytes = Path(record_path).read_bytes()
    record = json.loads(record_bytes)
    path = Path(data) / opsd.FILE_NAME
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    require(sha == opsd.FILE_SHA256, "OPSD_HASH", f"{path}: SHA-256 {sha}, expected {opsd.FILE_SHA256}")
    copies, removed = {}, {}
    for site in sorted(record["sites"]):
        hours = opsd.hourly(path, site)
        clean = opsd.clean_days(hours)
        found = week_copies(hours, clean)
        targets = {r["date"] for r in record["sites"][site]["rows"]}
        dates = sorted(d.isoformat() for d in found)
        solar_too = sum(all(round(hours[a][1], opsd.ROUNDING) == round(hours[b][1], opsd.ROUNDING)
                            for a, b in zip(opsd.local_day_hours(d - timedelta(days=lag)), clean[d]))
                        for d, lag in found.items())
        removed[site] = {d for d in dates if d in targets}
        copies[site] = {"clean_days": len(found), "target_days": len(removed[site]), "solar_identical_too": solar_too,
                        "by_lag": {str(lag): sum(v == lag for v in found.values()) for lag in LAGS},
                        "target_dates": sorted(removed[site])}
    result = sensitivity(record, removed)
    for site in result["sites"]:
        result["sites"][site]["week_copies"] = copies[site]
    result["identity"] = {"record_sha256": hashlib.sha256(record_bytes).hexdigest(), "file_sha256": sha,
                          "rule": f"grid import identical hour by hour (rounded to 1e-{opsd.ROUNDING} kWh) to the day {' or '.join(map(str, LAGS))} days earlier"}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", default=ROOT / "var" / "opsd", type=Path)
    parser.add_argument("--record", default=ROOT / "reports" / "opsd" / "rasid.json", type=Path)
    parser.add_argument("--out", default=ROOT / "reports" / "opsd" / "copies.json", type=Path)
    args = parser.parse_args(argv)
    result = check(args.data, args.record)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
