"""RASID on real load: two business sites from the Open Power System Data household package (CC BY 4.0).

The file holds cumulative meter readings; each row is labelled with the start of its hour and holds the reading at
that hour's end, so the energy of hour t is reading(t) - reading(t - 1h) (spec rule 1). Load is reconstructed as grid
import plus solar, which holds only because neither site exports (rule 4). Nothing here touches the network; the
file is fetched by scripts/fetch_opsd.py into the git-ignored var/opsd/.
"""
import csv
import copy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .core.contract import require

URL = "https://data.open-power-system-data.org/household_data/2020-04-15/household_data_60min_singleindex.csv"
FILE_NAME = "household_data_60min_singleindex.csv"
FILE_BYTES = 15654559
FILE_SHA256 = "15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60"
VERSION = "2020-04-15"
ZONE = "Europe/Berlin"
ATTRIBUTION = ("Open Power System Data. 2020. Data Package Household Data. Version 2020-04-15. "
               "https://data.open-power-system-data.org/household_data/2020-04-15/. Primary data from the CoSSMic project "
               "(Konstanz, Germany). CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. The load of each site is "
               "reconstructed as grid import plus solar; values were differenced from cumulative meter readings.")
SITES = {"industrial1": ("DE_KN_industrial1_grid_import", ("DE_KN_industrial1_pv_1", "DE_KN_industrial1_pv_2")),
         "industrial3": ("DE_KN_industrial3_grid_import", ("DE_KN_industrial3_pv_facade", "DE_KN_industrial3_pv_roof"))}
BUILDINGS = {"industrial1": "warehouse", "industrial3": "research-institute building"}
HOUR = timedelta(hours=1)
SCALED_ASSETS = ("capacity", "power", "initial", "terminal")
CHRONOS_REVISION = "a0e552de83495b5c28c14c71c374f3e33280b340"

# The sealed protocol (spec rules 1-12). Every choice the evaluation makes is here; its digest is fixed below and
# a real run refuses a protocol that differs from it. Changing anything is a new, separately labelled analysis.
PROTOCOL = {
    "analysis": "RASID on real load: two business sites from the Open Power System Data household package",
    "currency": "none: every cost is in RASID's illustrative tariff units",
    "data": {"url": URL, "file": FILE_NAME, "bytes": FILE_BYTES, "sha256": FILE_SHA256, "version": VERSION,
             "licence": "CC BY 4.0", "zone": ZONE,
             "sites": {site: {"building": BUILDINGS[site], "grid_import": imp, "solar": list(pv)} for site, (imp, pv) in SITES.items()}},
    "values": {"step_tolerance_kwh": 1e-9, "rounding_digits": 6, "flat_run_hours": 4, "lookback_days": 7,
               "target_day_hours": 24, "sunny_fraction_of_p99_solar": 0.2, "low_import_fraction_of_median_load": 0.02,
               "history_days": 300, "chronos_context_hours": 2048, "sizing_days": 28, "limit_factor": 2.0,
               "generated_site": "examples.electricity(102)", "block_days": 7, "draws": 4000, "seed": 2026},
    "rules": {
        "1_hourly_energy": ("Each row holds the cumulative reading at the end of the hour it is labelled with. The energy of "
                            "hour t is reading(t) - reading(t - 1h); it is missing when either row is absent or the rows are "
                            "not exactly one hour apart, when a difference is below -1e-9 kWh, or when a used column is "
                            "named in `interpolated` at t or at t - 1h."),
        "2_load": "Solar is the sum of the site's two solar columns; load is grid import plus solar (reconstructed).",
        "3_clean_day": ("A local day (Europe/Berlin) is clean when every one of its hours has all three energies, its grid "
                        "import is not identical hour by hour to the previous local day's, and its grid import has no run "
                        "of four or more identical non-zero hourly values; energies compared after rounding to 1e-6 kWh. "
                        "A clean hour is an hour of a clean day."),
        "4_no_export_gate": ("Over clean hours, any hour with solar above 0.2 x the 99th-percentile solar while grid import "
                             "is below 0.02 x the median load stops the run before anything is forecast."),
        "5_target_day": ("A clean day of 24 hours whose 7 previous local days are clean and which the ridge forecaster "
                         "accepts; a ridge refusal is counted by cause and the day is not a target. Target days form runs "
                         "of consecutive days."),
        "6_forecast_inputs": ("History: every clean hour before the origin (local midnight) within 300 days, known one hour "
                              "after it starts. Ridge: rasid.forecast.forecast(rows, origin, grid, 4) over the 24 local hours; "
                              "seasonal baseline: its seasonal_naive path; Chronos: models.chronos_forecaster on the contiguous "
                              "clean hours ending at the hour before the origin, at most 2048, under hf_evaluation.SCENARIO_RULE."),
        "7_battery_and_tariff": ("examples.settings(24) with capacity, power, initial and terminal charge times s = mean load "
                                 "over the site's first 28 clean days / the mean load of examples.electricity(102); grid limit "
                                 "2 x the site's largest clean hourly load; prices, risk 0.35, beta 0.90, efficiencies and "
                                 "cycles unchanged; time-of-use prices by local hour, as shipped."),
        "8_carried_state": ("Each policy carries its battery charge and month-to-date peak; the peak resets at each local "
                            "month's start; at the start of each run both reset to the initial charge (no_battery at 0) "
                            "and a peak of 0."),
        "9_measures": ("Per site: load MAE of ridge, baseline and Chronos median; pinball loss and truncated CRPS on 0.1, 0.5, "
                       "0.9; 80% coverage; decision_summary per policy; share of achievable battery value per policy with an "
                       "interval; paired daily cost differences for the comparisons below; paired MAE differences."),
        "10_bootstrap_and_pooling": ("Blocks of 7 consecutive target days inside one run, a shorter run as one block; draws "
                                     "pick blocks uniformly, concatenate and truncate to the site's day count; 4000 draws, "
                                     "seed 2026. Pooled: unit-free (cost differences / the site's mean daily no-battery cost, "
                                     "MAE / the site's mean load, share as is), each site weighted equally, sites resampled "
                                     "independently, normalisers fixed from all target days."),
        "11_readings": "Resolved when the 95% interval excludes zero; an interval touching zero is not resolved; no multiplicity adjustment.",
        "12_refusals": ("No plan within the planner's limit, a plan the verifier rejects, or a refused planner input: the day is "
                        "dropped for every policy and forecaster, recorded by cause and policy, and the run ends there."),
    },
    "forecasters": {"ridge": "rasid.forecast.forecast, as shipped", "seasonal_naive": "the ridge forecast's seasonal_naive path",
                    "chronos": f"amazon/chronos-bolt-tiny@{CHRONOS_REVISION}, levels 0.1 0.3 0.5 0.7 0.9"},
    "policies": ["candidate", "candidate_risk_neutral", "seasonal_naive", "perfect_foresight", "no_battery",
                 "chronos", "chronos_risk_neutral"],
    "comparisons": {"cost": [["candidate", "seasonal_naive"], ["candidate_risk_neutral", "seasonal_naive"],
                             ["candidate", "candidate_risk_neutral"], ["chronos_risk_neutral", "candidate_risk_neutral"],
                             ["chronos_risk_neutral", "seasonal_naive"], ["chronos", "candidate"]],
                    "accuracy": [["ridge_mae", "naive_mae"], ["chronos_mae", "ridge_mae"]]},
    "gate": {"generated_ridge": ("hf_evaluation.rasid_gate against reports/evaluation/rasid.json: MAE within 1e-9 kW, "
                                 "candidate totals within 5e-4 relative, other totals within 1e-6 relative, plan counts exact"),
             "generated_chronos": ("against the Chronos side of reports/hf/rasid.json: chronos_mae_kw, chronos_mean_pinball, "
                                   "chronos_truncated_crps and chronos_coverage_80_load within 1e-9; chronos and "
                                   "chronos_risk_neutral totals within 1e-6 relative; plan counts exact")},
}
SEALED_DIGEST = "e24cc97b71cae02f50b1067563563624365861064156d51bd01f1e9bb06ad78b"

_V = PROTOCOL["values"]
STEP_TOLERANCE = _V["step_tolerance_kwh"]          # a reading difference below this is a meter step backwards
ROUNDING = _V["rounding_digits"]                   # energies compared after rounding (copied and straight-line days)
FLAT_RUN = _V["flat_run_hours"]                    # identical non-zero hourly imports that make a straight-line fill
LOOKBACK_DAYS = _V["lookback_days"]                # a target day's previous clean days
SUNNY_FRACTION = _V["sunny_fraction_of_p99_solar"]
LOW_IMPORT_FRACTION = _V["low_import_fraction_of_median_load"]
SIZING_DAYS = _V["sizing_days"]                    # first clean days that size the battery
LIMIT_FACTOR = _V["limit_factor"]                  # grid limit, times the largest clean hourly load: never binds
HISTORY_DAYS = _V["history_days"]
CONTEXT_HOURS = _V["chronos_context_hours"]


def _float(value):
    return None if value in ("", None) else float(value)


def hourly(path: Path, site: str) -> dict:
    """UTC hour start -> (grid import kWh, solar kWh) for every hour whose energy rule 1 allows."""
    imp_col, pv_cols = SITES[site]
    cols = (imp_col, *pv_cols)
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["utc_timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
            flagged = {s.strip() for s in (row.get("interpolated") or "").split("|") if s.strip()}
            rows.append((t, tuple(_float(row[c]) for c in cols), bool(flagged & set(cols))))
    out = {}
    for (t0, v0, f0), (t1, v1, f1) in zip(rows, rows[1:]):
        if t1 - t0 != HOUR or f0 or f1 or None in v0 or None in v1:
            continue
        diffs = [b - a for a, b in zip(v0, v1)]
        if min(diffs) < -STEP_TOLERANCE:
            continue
        out[t1] = (diffs[0], diffs[1] + diffs[2])
    return out


def local_day_hours(day: date, zone=ZONE) -> list:
    """The UTC hour starts of one local day: 23, 24 or 25 of them."""
    z = ZoneInfo(zone)
    a = datetime.combine(day, datetime.min.time(), tzinfo=z).astimezone(timezone.utc)
    b = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=z).astimezone(timezone.utc)
    return [a + k * HOUR for k in range(int((b - a) / HOUR))]


def _flat(values):
    run = best = 1
    for a, b in zip(values, values[1:]):
        run = run + 1 if (a == b and a != 0) else 1
        best = max(best, run)
    return best >= FLAT_RUN


def clean_days(hours: dict, zone=ZONE) -> dict:
    """Local date -> its UTC hours, for every clean day (rule 3): every hour present, grid import not identical
    hour by hour to the previous local day's, and no run of four or more identical non-zero hourly imports."""
    if not hours:
        return {}
    z = ZoneInfo(zone)
    first = min(hours).astimezone(z).date() - timedelta(days=1)
    last = max(hours).astimezone(z).date()
    out, previous = {}, None
    day = first
    while day <= last:
        ts = local_day_hours(day, zone)
        complete = all(t in hours for t in ts)
        imports = [round(hours[t][0], ROUNDING) for t in ts] if complete else None
        if complete and imports != previous and not _flat(imports):
            out[day] = ts
        previous = imports
        day += timedelta(days=1)
    return out


def _clean_hours(hours, clean):
    return [t for day in sorted(clean) for t in clean[day]]


def export_gate(hours: dict, clean: dict) -> dict:
    """Rule 4: over clean hours, no hour with solar above 20% of the 99th-percentile solar while grid import is below
    2% of the median load. Load = import + solar holds only if the site never exports."""
    ts = _clean_hours(hours, clean)
    imp = np.array([hours[t][0] for t in ts])
    pv = np.array([hours[t][1] for t in ts])
    solar_threshold = SUNNY_FRACTION * float(np.quantile(pv, .99))
    import_threshold = LOW_IMPORT_FRACTION * float(np.median(imp + pv))
    sunny = pv > solar_threshold
    low = sunny & (imp < import_threshold)
    return {"sunny_hours": int(sunny.sum()), "low_import_hours": int(low.sum()), "passed": not bool(low.any()),
            "solar_threshold_kw": solar_threshold, "import_threshold_kw": import_threshold}


def target_candidates(clean: dict) -> list:
    """Rule 5, before the ridge check: 24-hour clean days whose seven previous local days are clean."""
    return sorted(d for d, ts in clean.items()
                  if len(ts) == 24 and all(d - timedelta(days=k) in clean for k in range(1, LOOKBACK_DAYS + 1)))


def runs(days: list) -> list:
    """Maximal runs of consecutive dates, in order."""
    out = []
    for d in sorted(days):
        if out and d - out[-1][-1] == timedelta(days=1):
            out[-1].append(d)
        else:
            out.append([d])
    return out


def generated_mean_load() -> float:
    """The generated site's mean load over the 102 days the shipped evaluation reads (67.214 kW)."""
    from . import examples
    return float(np.mean([r["load"] for r in examples.electricity(102)]))


def site_base(hours: dict, clean: dict) -> dict:
    """Rule 7: examples.settings(24) with capacity, power, initial and terminal charge scaled by
    s = mean load over the first 28 clean days / the generated site's mean load; the grid limit is twice the largest
    clean hourly load, so it never binds; prices, risk, beta, efficiencies and cycles are unchanged."""
    from . import examples
    days = sorted(clean)
    require(len(days) >= SIZING_DAYS, "OPSD_SIZING", f"Sizing needs {SIZING_DAYS} clean days; the site has {len(days)}")
    first = [t for d in days[:SIZING_DAYS] for t in clean[d]]
    scale = float(np.mean([sum(hours[t]) for t in first])) / generated_mean_load()
    limit = LIMIT_FACTOR * max(sum(hours[t]) for t in _clean_hours(hours, clean))
    settings = copy.deepcopy(examples.settings(24))
    for k in SCALED_ASSETS:
        settings["assets"][k] *= scale
    settings["assets"]["limit"] = limit
    return {"scale": scale, "limit_kw": limit, "settings": settings}


def history_rows(hours: dict, clean: dict, origin: datetime, days=HISTORY_DAYS) -> list:
    """Rule 6: every clean hour before the origin within `days`, in rasid.forecast.records format, known one hour
    after the hour starts (at its end)."""
    lo = origin - timedelta(days=days)
    return [{"time": t.isoformat(), "known_at": (t + HOUR).isoformat(), "load": hours[t][0] + hours[t][1], "pv": hours[t][1]}
            for t in _clean_hours(hours, clean) if lo <= t < origin]
