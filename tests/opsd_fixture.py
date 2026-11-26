"""A small stand-in for the OPSD household 60-minute file: the real column names of both business sites, cumulative
readings at each hour's end, and one of each defect the data rules must catch, on dates the tests name."""
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HOUR = timedelta(hours=1)
COLUMNS = {"industrial1": ("DE_KN_industrial1_grid_import", "DE_KN_industrial1_pv_1", "DE_KN_industrial1_pv_2"),
           "industrial3": ("DE_KN_industrial3_grid_import", "DE_KN_industrial3_pv_facade", "DE_KN_industrial3_pv_roof")}
FLAG_DAY = date(2016, 3, 5)      # one industrial1 import hour named in `interpolated`
COPY_DAY = date(2016, 3, 8)      # hour-by-hour import identical to the day before
FLAT_DAY = date(2016, 3, 11)     # five identical non-zero hourly imports (a straight-line fill)
NEG_DAY = date(2016, 3, 14)      # industrial1 import meter steps backwards once
MISSING_DAY = date(2016, 3, 17)  # one row absent for every column
DST_DAY = date(2016, 3, 27)      # Europe/Berlin springs forward: 23 local hours
OFFSET = {date(2016, 3, d): 1 for d in range(1, 32)} | {date(2016, 4, d): 2 for d in range(1, 31)}  # UTC offset (h)


def _local_noon_utc(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc) - timedelta(hours=OFFSET.get(day, 1))


def make_opsd_csv(path: Path, *, days=40, start="2016-03-01") -> Path:
    """Write the stand-in CSV and return its path. Hourly energies: load 40–90 kWh, solar 0–10 kWh (never above load,
    so the sites never export); industrial3 is industrial1 doubled. Rows run from the day before `start` to the day
    after the last one, in UTC; each row holds the cumulative readings at the end of its hour."""
    first = datetime.fromisoformat(start).replace(tzinfo=timezone.utc) - timedelta(days=1)
    n = (days + 2) * 24
    rng = np.random.default_rng(7)
    times = [first + i * HOUR for i in range(n)]
    imp = np.empty(n)
    pv = np.empty(n)
    for i, t in enumerate(times):
        local = t + timedelta(hours=1)          # near enough to shape the day; the defects use exact local days
        h = local.hour + local.minute / 60
        load = 40 + 50 * np.exp(-((h - 13) / 4) ** 2) + (i // 24) * 0.05 + rng.normal(0, 0.8)
        solar = max(0.0, 10 * np.sin((h - 6) * np.pi / 12)) if 6 <= h <= 18 else 0.0
        imp[i], pv[i] = round(load - solar, 6), round(solar, 6)
    index = {t: i for i, t in enumerate(times)}

    def local_hours(day):
        start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=OFFSET.get(day, 1))
        end_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1) - timedelta(
            hours=OFFSET.get(day + timedelta(days=1), 1))
        ts = [start_utc + k * HOUR for k in range(int((end_utc - start_utc) / HOUR))]
        return [index[t] for t in ts] if all(t in index for t in ts) else None

    def inside(*days_):
        return all(local_hours(d) is not None for d in days_)

    if inside(COPY_DAY - timedelta(days=1), COPY_DAY):
        imp[local_hours(COPY_DAY)] = imp[local_hours(COPY_DAY - timedelta(days=1))]
    if inside(FLAT_DAY):
        flat = local_hours(FLAT_DAY)[8:13]
        imp[flat] = imp[flat[0]]
    readings = {}
    for site, (ci, c1, c2) in COLUMNS.items():
        k = 1 if site == "industrial1" else 2
        readings[ci] = np.cumsum(imp * k) + 1000.0
        readings[c1] = np.cumsum(pv * k * 0.6) + 10.0
        readings[c2] = np.cumsum(pv * k * 0.4) + 20.0
    if inside(NEG_DAY):
        neg = local_hours(NEG_DAY)[10]
        readings[COLUMNS["industrial1"][0]][neg] = readings[COLUMNS["industrial1"][0]][neg - 1] - 5.0
    flagged = {local_hours(FLAG_DAY)[9]: COLUMNS["industrial1"][0]} if inside(FLAG_DAY) else {}
    missing = local_hours(MISSING_DAY)[12] if inside(MISSING_DAY) else None
    fields = ["utc_timestamp", "cet_cest_timestamp", *[c for cols in COLUMNS.values() for c in cols],
              "DE_KN_public1_grid_import", "interpolated"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, t in enumerate(times):
            if i == missing:
                continue
            row = {"utc_timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "cet_cest_timestamp": "", "DE_KN_public1_grid_import": "",
                   "interpolated": flagged.get(i, "") and f"DE_KN_residential3_freezer | {flagged[i]}"}
            row.update({c: f"{readings[c][i]:.6f}" for cols in COLUMNS.values() for c in cols})
            w.writerow(row)
    return path
