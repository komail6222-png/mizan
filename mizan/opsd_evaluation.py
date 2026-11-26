"""RASID on real load (OPSD): within-run statistics, the day loop, the generated-site gate and the evidence record.

Days are dependent through the battery charge and the month-to-date peak each policy carries, so intervals come from
a moving-block bootstrap whose blocks never span a gap between runs of target days (spec rule 10). The Chronos
forecaster is injected, so this module and its tests stay torch-free.
"""
import numpy as np

from .core.contract import require
from .opsd import PROTOCOL
from .visa_evaluation import reading

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
