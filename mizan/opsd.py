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
STEP_TOLERANCE = 1e-9      # a reading difference below -1e-9 is a meter step backwards: the hour is missing
ROUNDING = 6               # energies are compared after rounding to 1e-6 kWh (copied and straight-line days)
FLAT_RUN = 4               # four or more identical non-zero hourly imports: a straight-line fill
LOOKBACK_DAYS = 7          # a target day's previous clean days
SUNNY_FRACTION = .2        # of the 99th-percentile solar
LOW_IMPORT_FRACTION = .02  # of the median load
SIZING_DAYS = 28           # first clean days that size the battery
LIMIT_FACTOR = 2.0         # grid limit, times the largest clean hourly load: never binds
SCALED_ASSETS = ("capacity", "power", "initial", "terminal")


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


def history_rows(hours: dict, clean: dict, origin: datetime, days=300) -> list:
    """Rule 6: every clean hour before the origin within `days`, in rasid.forecast.records format, known one hour
    after the hour starts (at its end)."""
    lo = origin - timedelta(days=days)
    return [{"time": t.isoformat(), "known_at": (t + HOUR).isoformat(), "load": hours[t][0] + hours[t][1], "pv": hours[t][1]}
            for t in _clean_hours(hours, clean) if lo <= t < origin]
