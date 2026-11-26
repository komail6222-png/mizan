"""BASIRA on real images (VisA): statistics, the product-line harness and the evidence record. Torch-free: model
feature functions are injected, so CI runs every path with stand-ins.

The protocol is sealed in mizan.visa.PROTOCOL; docs/superpowers/specs/2026-11-25-basira-visa-design.md argues it.
"""
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np
from PIL import Image
from scipy import stats

import base64
import io

from . import examples
from .basira import engine as b
from .core.contract import digest
from .visa import ATTRIBUTION, CLASSES, PROTOCOL, TYPES, engine_png, protocol_split

ROOT = Path(__file__).resolve().parents[1]
ALPHA = Fraction(1, 20)
DELTA = Fraction(1, 10)
GUARANTEE = {"marginal": None, "pac": {"type": "pac", "delta": "0.1"}}
SCORE_RELATIVE_TOLERANCE = 1e-6


class Scorer(NamedTuple):
    """A feature function and the profile string b.fit binds into the bank identity. Engine-path scorers receive the
    decoded 96 px greyscale image; the native scorer receives the original image file, opened with PIL."""
    name: str
    features: Callable
    engine_path: bool
    profile: str


DESCRIPTOR = Scorer("descriptor", b.features, True, b.DESCRIPTOR)


def clopper_pearson(x, n, level=.95):
    """Exact two-sided interval for a binomial proportion x/n."""
    tail = (1 - level) / 2
    lo = 0.0 if x == 0 else float(stats.beta.ppf(tail, x, n - x + 1))
    hi = 1.0 if x == n else float(stats.beta.ppf(1 - tail, x + 1, n - x))
    return lo, hi


def rule_rank(m, rule):
    """The calibration rank the engine's rule thresholds at: marginal ceil((m+1)(1-alpha)), or the PAC rank (None
    when m is too small for it)."""
    if rule == "marginal":
        return math.ceil((m + 1) * (1 - ALPHA))
    if rule == "pac":
        return b.pac_rank(m, ALPHA, DELTA)
    raise ValueError(rule)


def exchangeability(classes, m, k):
    """What the rule implies if the test normals are exchangeable with the calibration normals.

    The long-run false-alert rate of the rank-k-of-m threshold is Beta(m-k+1, k); a class with n test normals then
    has Beta-Binomial(n, m-k+1, k) false alerts, and the pooled count is the exact convolution over classes.
    `classes` is [(n_test_normals, false_alerts), ...]. The verdict reads the observed pooled count against the
    distribution's central 95% range."""
    a, bb = m - k + 1, k
    pmf = np.array([1.0])
    for n, _ in classes:
        pmf = np.convolve(pmf, stats.betabinom.pmf(np.arange(n + 1), n, a, bb))
    cdf = np.cumsum(pmf)
    observed = sum(x for _, x in classes)
    lo = int(np.searchsorted(cdf, .025 - 1e-15))
    hi = int(np.searchsorted(cdf, .975 - 1e-15))
    at_most = float(cdf[observed])
    at_least = float(1 - cdf[observed - 1]) if observed > 0 else 1.0
    verdict = "more" if observed > hi else "fewer" if observed < lo else "consistent"
    return {"observed": observed, "n": sum(n for n, _ in classes), "m": m, "k": k,
            "expected_mean": sum(n for n, _ in classes) * a / (m + 1), "range95": [lo, hi],
            "p_two_sided": min(1.0, 2 * min(at_most, at_least)), "verdict": verdict, "pmf": pmf.tolist()}


def auroc(normal, anomaly):
    """Probability that an anomaly scores above a normal item, ties counting one half (Mann-Whitney)."""
    normal, anomaly = np.asarray(normal, float), np.asarray(anomaly, float)
    ranks = stats.rankdata(np.concatenate([normal, anomaly]))
    u = ranks[len(normal):].sum() - len(anomaly) * (len(anomaly) + 1) / 2
    return float(u / (len(normal) * len(anomaly)))


def auroc_interval(normal, anomaly, draws=2000, seed=2026):
    """95% percentile interval from resampling the normal and the anomalous items separately."""
    normal, anomaly = np.asarray(normal, float), np.asarray(anomaly, float)
    rng = np.random.default_rng(seed)
    values = [auroc(normal[rng.integers(0, len(normal), len(normal))], anomaly[rng.integers(0, len(anomaly), len(anomaly))])
              for _ in range(draws)]
    lo, hi = np.quantile(values, [.025, .975])
    return float(lo), float(hi)


def reading(lo, hi):
    return "higher" if lo > 0 else "lower" if hi < 0 else "not resolved"


def paired_difference(strata, stat, draws=2000, seed=2026):
    """A paired bootstrap of stat(strata) = (scorer a - scorer b). Each stratum maps names to equal-length per-item
    arrays; one draw resamples item indices within every stratum and applies them to all of its arrays, so the two
    scorers stay paired on the same items."""
    rng = np.random.default_rng(seed)
    strata = [{k: (np.asarray(v) if not isinstance(v, str) else v) for k, v in s.items()} for s in strata]
    def resample(s):
        n = len(s["a"])
        idx = rng.integers(0, n, n)
        return {k: (v[idx] if isinstance(v, np.ndarray) else v) for k, v in s.items()}
    values = [stat([resample(s) for s in strata]) for _ in range(draws)]
    lo, hi = (float(v) for v in np.quantile(values, [.025, .975]))
    return {"difference": float(stat(strata)), "ci95": [lo, hi], "reading": reading(lo, hi),
            "bootstrap": f"paired, stratified, {draws} draws, seed {seed}"}


# ---------------------------------------------------------------------------------------------- product lines

def _decoded(root, source):
    """The engine's 96 px greyscale image of a source: a VisA path (relative to root) or a ready {group, png} item."""
    return b.decode_image(source["png"] if isinstance(source, dict) else engine_png(Path(root) / source))


def _item(root, source):
    return source if isinstance(source, dict) else {"group": source, "png": engine_png(Path(root) / source)}


def _name(source):
    return source["group"] if isinstance(source, dict) else source


def leaks(root, split):
    """Test items whose decoded 96 px image equals a reference or calibration image: the engine refuses such a query
    (QUERY_LEAK), so every scorer excludes them and the record counts them."""
    train = {digest(_decoded(root, p).tobytes()) for p in split["reference"] + split["calibration"]}
    return {p: "QUERY_LEAK" for p in split["test_normal"] + split["test_anomaly"]
            if digest(_decoded(root, p).tobytes()) in train}


def _fit(root, config, scorer):
    if scorer.engine_path:
        bank, cal, ident, _, _ = b.fit([_item(root, s) for s in config["reference"]],
                                       [_item(root, s) for s in config["calibration"]], scorer.features, scorer.profile)
        return bank, cal, ident
    def features(source):
        with Image.open(Path(root) / source) as im:
            im.load()
            return scorer.features(im)
    bank = np.stack([features(s) for s in config["reference"]])
    cal = sorted(b.score(features(s), bank)[0] for s in config["calibration"])
    return bank, cal, digest([scorer.profile, list(config["reference"]), list(config["calibration"]), cal])


def product_line(root, configs, tests, scorer, *, refuse=None):
    """Fit each configuration's bank and calibration, then score every test item once against every bank.

    configs: {name: {"reference": [...], "calibration": [...], "rules": ["marginal"] or ["marginal", "pac"]}};
    tests: [(source, label)]; refuse: {source name: code} for items excluded on every scorer. Decisions always come
    from b.rank_decision at alpha .05, exactly as the engine makes them."""
    refuse = refuse or {}
    fitted = {name: _fit(root, c, scorer) for name, c in configs.items()}
    rows = []
    for source, label in tests:
        name = _name(source)
        row = {"path": name, "label": label, "refused": refuse.get(name), "scores": {}, "p_values": {}, "flags": {}}
        if row["refused"]:
            row["scores"] = {c: None for c in configs}
            row["p_values"] = {c: None for c in configs}
            row["flags"] = {c: {} for c in configs}
            rows.append(row)
            continue
        if scorer.engine_path:
            feat = scorer.features(_decoded(root, source))
        else:
            with Image.open(Path(root) / source) as im:
                im.load()
                feat = scorer.features(im)
        for c, (bank, cal, _) in fitted.items():
            s = b.score(feat, bank)[0]
            row["scores"][c] = s
            row["flags"][c] = {}
            for rule in configs[c]["rules"]:
                decision = b.rank_decision(cal, s, ".05", GUARANTEE[rule])
                row["flags"][c][rule] = decision["decision"] == "FLAG"
                if rule == "marginal":
                    row["p_values"][c] = decision["p_value"]
        rows.append(row)
    return {"scorer": scorer.name, "profile": scorer.profile,
            "configs": {c: {"bank_digest": ident, "m": len(cal), "calibration_scores": cal,
                            "reference": len(configs[c]["reference"])} for c, (_, cal, ident) in fitted.items()},
            "rows": rows}


# ---------------------------------------------------------------------------------------------- fidelity gate

def _frozen_tests():
    conditions = [None if i < 40 else ("pit" if i % 2 else "crack") for i in range(80)]
    tests = [({"group": f"frozen-{9100 + i}", "png": b.encode_image(examples.part(9100 + i, d))},
              "normal" if d is None else "anomaly") for i, d in enumerate(conditions)]
    return tests, conditions


def frozen_record(scorer):
    """The shipped frozen test (bank seeds 0-9, calibration 100-123, items 9100-9179) through product_line, in the
    shape of reports/evaluation/basira.json and the model frozen test of reports/hf/basira.json."""
    p = examples.inspection()
    tests, conditions = _frozen_tests()
    line = product_line(None, {"shipped": {"reference": p["reference"], "calibration": p["calibration"],
                                           "rules": ["marginal"]}}, tests, scorer)
    rows = [{"seed": 9100 + i, "condition": d or "intact", "score": r["scores"]["shipped"],
             "p_value": r["p_values"]["shipped"], "flag": r["flags"]["shipped"]["marginal"]}
            for i, (d, r) in enumerate(zip(conditions, line["rows"]))]
    return {"calibration_m": line["configs"]["shipped"]["m"], "bank_digest": line["configs"]["shipped"]["bank_digest"],
            "detected": sum(r["flag"] for r in rows[40:]), "false_alerts": sum(r["flag"] for r in rows[:40]), "rows": rows}


def _check(checked, mismatches, name, got, expected, ok=None):
    checked.append(name)
    if not (got == expected if ok is None else ok):
        mismatches.append({"name": name, "expected": expected, "got": got})


def fixture_gate(scorer=DESCRIPTOR, shipped=None):
    """The harness must reproduce the shipped frozen test exactly: bank digest, calibration m, and every row's seed,
    condition, p-value and flag."""
    shipped = shipped or json.loads((ROOT / "reports/evaluation/basira.json").read_text(encoding="utf-8"))
    got = frozen_record(scorer)
    checked, mismatches = [], []
    _check(checked, mismatches, "bank_digest", got["bank_digest"], shipped["bank_digest"])
    _check(checked, mismatches, "calibration_m", got["calibration_m"], shipped["calibration_m"])
    for i, (g, s) in enumerate(zip(got["rows"], shipped["rows"])):
        for key in ("seed", "condition", "p_value", "flag"):
            _check(checked, mismatches, f"row[{i}].{key}", g[key], s[key])
    _check(checked, mismatches, "rows", len(got["rows"]), len(shipped["rows"]))
    return {"passed": not mismatches, "checked": checked, "mismatches": mismatches, "bit_exact": not mismatches,
            "rule": "exact equality with the frozen test of reports/evaluation/basira.json"}


def dinov2_gate(record, shipped):
    """3.1's DINOv2 frozen test through this harness: calibration m, p-values (hence ranks), flags and counts must be
    exact; scores within 1e-6 relative; the bank digest is exempt because it hashes float calibration scores that
    another CPU's math libraries can move in the last bits. bit_exact says whether everything matched exactly."""
    checked, mismatches = [], []
    for key in ("calibration_m", "detected", "false_alerts"):
        _check(checked, mismatches, key, record[key], shipped[key])
    _check(checked, mismatches, "rows", len(record["rows"]), len(shipped["rows"]))
    exact = record["bank_digest"] == shipped["bank_digest"]
    for i, (g, s) in enumerate(zip(record["rows"], shipped["rows"])):
        for key in ("seed", "condition", "p_value", "flag"):
            _check(checked, mismatches, f"row[{i}].{key}", g[key], s[key])
        close = abs(g["score"] - s["score"]) <= SCORE_RELATIVE_TOLERANCE * max(abs(s["score"]), 1e-300)
        _check(checked, mismatches, f"row[{i}].score", g["score"], s["score"], close)
        exact = exact and g["score"] == s["score"]
    return {"passed": not mismatches, "checked": checked, "mismatches": mismatches, "bit_exact": exact and not mismatches,
            "rule": "calibration m, p-values, flags and counts exact; scores within 1e-6 relative; bank digest exempt"}


# ---------------------------------------------------------------------------------------------- study and examples

def study(draw_scores, eval_scores, anomaly_scores, *, draws=400, sizes=(24, 50, 100), seed=23):
    """Repeat calibration on one fixed bank: draw m calibration scores from the draw pool, threshold at the rule's rank,
    and measure that threshold's long-run false-alert rate on the evaluation pool and its detection rate on the test
    anomalies. A size larger than the draw pool is skipped and noted."""
    draw_scores, eval_scores, anomaly_scores = (np.asarray(x, float) for x in (draw_scores, eval_scores, anomaly_scores))
    rng = np.random.default_rng(seed)
    def rates(threshold):
        if threshold is None:
            return 0.0, 0.0
        return float(np.mean(eval_scores > threshold)), float(np.mean(anomaly_scores > threshold))
    rows, capped = [], []
    for m in sizes:
        if m > len(draw_scores):
            capped.append({"m": m, "pool": len(draw_scores)})
            continue
        k, kp = rule_rank(m, "marginal"), rule_rank(m, "pac")
        marginal, pac = [], []
        for _ in range(draws):
            cal = np.sort(rng.choice(draw_scores, m, replace=False))
            marginal.append(rates(cal[k - 1] if k <= m else None))
            pac.append(rates(cal[kp - 1]) if kp else (None, None))
        fa = np.array([x[0] for x in marginal])
        row = {"m": m, "draws": draws, "marginal_rank": k, "mean_false_alert": float(fa.mean()),
               "share_above_alpha": float(np.mean(fa > float(ALPHA))),
               "theoretical_share_above_alpha": float(b.exceedance(m, k, ALPHA)) if k <= m else None,
               "mean_detection": float(np.mean([x[1] for x in marginal])), "pac_rank": kp}
        if kp:
            fp = np.array([x[0] for x in pac])
            row.update(pac_mean_false_alert=float(fp.mean()), pac_share_above_alpha=float(np.mean(fp > float(ALPHA))),
                       pac_theoretical_share_above_alpha=float(b.exceedance(m, kp, ALPHA)),
                       pac_mean_detection=float(np.mean([x[1] for x in pac])))
        rows.append(row)
    return {"rows": rows, "capped": capped, "pools": {"draw": len(draw_scores), "evaluation": len(eval_scores),
                                                      "anomalies": len(anomaly_scores)}}


SETS = {"caught": ("anomaly", True), "missed": ("anomaly", False), "false_alert": ("normal", True), "passed": ("normal", False)}


def pick_examples(rows_by_class):
    """For each outcome, the item whose score is the lower median of that outcome's items, ties broken by (class,
    path); None when no item has that outcome. rows_by_class: {class: [{path, label, score, flag}]}."""
    picked = {}
    for name, (label, flag) in SETS.items():
        items = sorted(((r["score"], cls, r["path"]) for cls, rows in rows_by_class.items() for r in rows
                        if r["label"] == label and r["flag"] == flag), key=lambda t: (t[0], t[1], t[2]))
        if items:
            score, cls, path = items[(len(items) - 1) // 2]
            picked[name] = {"class": cls, "path": path, "score": score}
        else:
            picked[name] = None
    return picked


def _jpeg(root, path):
    buf = io.BytesIO()
    _decoded(root, path).save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------------------------- the record

RULES = (("as_shipped", "as_shipped", "marginal"), ("full", "full", "marginal"), ("full_pac", "full", "pac"))
QUICK = {"split": (2, 2), "as_shipped": (2, 1), "draws": 20, "sizes": (1, 2), "bootstrap": 50}   # b.fit needs >= 2 reference
COMPARISONS = (("dinov2-engine", "descriptor"), ("dinov2-native", "dinov2-engine"))


def _rate(x, n):
    return {"x": int(x), "n": int(n), "rate": (x / n) if n else None, "ci95": list(clopper_pearson(x, n)) if n else None}


def _counts(rows, config, rule):
    live = [r for r in rows if not r["refused"]]
    fa = sum(r["flags"][config][rule] for r in live if r["label"] == "normal")
    det = sum(r["flags"][config][rule] for r in live if r["label"] == "anomaly")
    return (fa, sum(r["label"] == "normal" for r in live)), (det, sum(r["label"] == "anomaly" for r in live))


def _strip(check):
    return {k: v for k, v in check.items() if k != "pmf"}


def evaluate_visa(root, manifest, scorers, *, quick=False):
    """Every product line under the sealed protocol, scored by every scorer: the evidence record without the runner's
    gate, repeat_run and environment. quick shrinks only sizes and draws, for tests on the generated stand-in."""
    split_sizes = QUICK["split"] if quick else None
    shipped_ref, shipped_cal = QUICK["as_shipped"] if quick else (PROTOCOL["configurations"]["as_shipped"]["reference"],
                                                                 PROTOCOL["configurations"]["as_shipped"]["calibration"])
    draws = QUICK["draws"] if quick else PROTOCOL["study"]["draws"]
    sizes = QUICK["sizes"] if quick else tuple(PROTOCOL["study"]["sizes"])
    boot = QUICK["bootstrap"] if quick else PROTOCOL["statistics"]["auroc"]["draws"]
    classes = [c for c in CLASSES if any(x["object"] == c for x in manifest["images"])]
    lines, splits, pools = {s.name: {} for s in scorers}, {}, {s.name: {} for s in scorers}
    for cls in classes:
        sp = protocol_split(root, manifest, cls, sizes=split_sizes)
        refuse = leaks(root, sp)
        configs = {"as_shipped": {"reference": sp["reference"][:shipped_ref], "calibration": sp["calibration"][:shipped_cal],
                                  "rules": ["marginal"]},
                   "full": {"reference": sp["reference"], "calibration": sp["calibration"], "rules": ["marginal", "pac"]}}
        tests = [(p, "normal") for p in sp["test_normal"]] + [(p, "anomaly") for p in sp["test_anomaly"]]
        extra = [(p, "draw") for p in sp["draw_pool"]] + [(p, "eval") for p in sp["eval_pool"]]
        splits[cls] = {"type": TYPES[cls], "seed": sp["seed"], "duplicates_dropped": sp["duplicates_dropped"],
                       "refused": len(refuse), **{k: len(sp[k]) for k in ("reference", "calibration", "draw_pool",
                                                                          "eval_pool", "test_normal", "test_anomaly")},
                       "list_digests": {k: digest(sp[k]) for k in ("reference", "calibration", "draw_pool", "eval_pool")}}
        for s in scorers:
            line = product_line(root, configs, tests + extra, s, refuse=refuse)
            pools[s.name][cls] = {lab: [r["scores"]["full"] for r in line["rows"] if r["label"] == lab] for lab in ("draw", "eval")}
            line["rows"] = [r for r in line["rows"] if r["label"] in ("normal", "anomaly")]
            lines[s.name][cls] = line

    per_class, pooled, exch = {}, {}, {}
    for s in scorers:
        per_class[s.name], pooled[s.name], exch[s.name] = {}, {}, {}
        for key, config, rule in RULES:
            by_class, fa_by_type, det_by_type, counts = {}, {}, {}, []
            for cls in classes:
                line = lines[s.name][cls]
                (fx, fn), (dx, dn) = _counts(line["rows"], config, rule)
                m = line["configs"][config]["m"]
                k = rule_rank(m, rule)
                by_class[cls] = {"false_alert": _rate(fx, fn), "detection": _rate(dx, dn), "m": m, "k": k,
                                 "bank_digest": line["configs"][config]["bank_digest"],
                                 "refused": sum(bool(r["refused"]) for r in line["rows"])}
                counts.append((cls, m, k, fn, fx))
                t = TYPES[cls]
                fa_by_type.setdefault(t, [0, 0]); det_by_type.setdefault(t, [0, 0])
                fa_by_type[t][0] += fx; fa_by_type[t][1] += fn; det_by_type[t][0] += dx; det_by_type[t][1] += dn
            per_class[s.name][key] = by_class
            pooled[s.name][key] = {
                "false_alert": _rate(sum(c["false_alert"]["x"] for c in by_class.values()), sum(c["false_alert"]["n"] for c in by_class.values())),
                "detection": _rate(sum(c["detection"]["x"] for c in by_class.values()), sum(c["detection"]["n"] for c in by_class.values())),
                "by_type": {t: {"false_alert": _rate(*fa_by_type[t]), "detection": _rate(*det_by_type[t])} for t in fa_by_type}}
            ms = {(m, k) for _, m, k, _, _ in counts}
            if len(ms) == 1 and next(iter(ms))[1] is not None and next(iter(ms))[1] <= next(iter(ms))[0]:
                m, k = next(iter(ms))
                exch[s.name][key] = {"pooled": _strip(exchangeability([(n, x) for _, _, _, n, x in counts], m, k)),
                                     "by_type": {t: _strip(exchangeability([(n, x) for c, _, _, n, x in counts if TYPES[c] == t], m, k))
                                                 for t in sorted({TYPES[c] for c in classes})}}
            else:
                exch[s.name][key] = {"applicable": False, "reason": "no finite threshold at this calibration size"}

    def full_scores(name, cls, label):
        return [r["scores"]["full"] for r in lines[name][cls]["rows"] if r["label"] == label and not r["refused"]]
    auc = {}
    for s in scorers:
        per = {}
        for i, cls in enumerate(classes):
            normal, anomaly = full_scores(s.name, cls, "normal"), full_scores(s.name, cls, "anomaly")
            per[cls] = {"auroc": auroc(normal, anomaly), "ci95": list(auroc_interval(normal, anomaly, draws=boot,
                                                                                       seed=PROTOCOL["statistics"]["auroc"]["seed"]))}
        strata = [{"a": np.asarray(full_scores(s.name, c, lab)), "b": np.asarray(full_scores(s.name, c, lab)), "cls": c, "label": lab}
                  for c in classes for lab in ("normal", "anomaly")]
        def mean_auc(st):
            by = {}
            for x in st:
                by.setdefault(x["cls"], {})[x["label"]] = x["a"]
            return float(np.mean([auroc(v["normal"], v["anomaly"]) for v in by.values()]))
        mean = paired_difference(strata, mean_auc, draws=boot, seed=PROTOCOL["statistics"]["auroc"]["seed"])
        auc[s.name] = {"per_class": per, "mean": mean["difference"], "mean_ci95": mean["ci95"]}

    names = {s.name for s in scorers}
    paired = []
    for a, bname in COMPARISONS:
        if not {a, bname} <= names:
            continue
        strata = []
        for c in classes:
            ra = {r["path"]: r for r in lines[a][c]["rows"] if not r["refused"]}
            rb = {r["path"]: r for r in lines[bname][c]["rows"] if not r["refused"]}
            for lab in ("normal", "anomaly"):
                paths = sorted(p for p, r in ra.items() if r["label"] == lab and p in rb)
                strata.append({"cls": c, "label": lab,
                               "a": np.array([ra[p]["scores"]["full"] for p in paths]),
                               "b": np.array([rb[p]["scores"]["full"] for p in paths]),
                               "flag_a": np.array([ra[p]["flags"]["full"]["marginal"] for p in paths], dtype=float),
                               "flag_b": np.array([rb[p]["flags"]["full"]["marginal"] for p in paths], dtype=float)})
        def pooled_flags(label):
            def stat(st):
                xs = [x for x in st if x["label"] == label]
                return float(np.concatenate([x["flag_a"] for x in xs]).mean() - np.concatenate([x["flag_b"] for x in xs]).mean())
            return stat
        def auc_gap(st):
            by = {}
            for x in st:
                by.setdefault(x["cls"], {})[x["label"]] = x
            return float(np.mean([auroc(v["normal"]["a"], v["anomaly"]["a"]) - auroc(v["normal"]["b"], v["anomaly"]["b"])
                                  for v in by.values()]))
        seed = PROTOCOL["statistics"]["paired"]["seed"]
        draws_p = QUICK["bootstrap"] if quick else PROTOCOL["statistics"]["paired"]["draws"]
        paired.append({"comparison": f"{a} - {bname}", "a": a, "b": bname, "at": "full, marginal",
                       "detection": paired_difference(strata, pooled_flags("anomaly"), draws=draws_p, seed=seed),
                       "false_alert": paired_difference(strata, pooled_flags("normal"), draws=draws_p, seed=seed),
                       "auroc": paired_difference(strata, auc_gap, draws=draws_p, seed=seed)})

    studies = {}
    for s in scorers:
        per = {cls: study(pools[s.name][cls]["draw"], pools[s.name][cls]["eval"], full_scores(s.name, cls, "anomaly"),
                          draws=draws, sizes=sizes, seed=PROTOCOL["study"]["seed"]) for cls in classes}
        pooled_rows = []
        for m in sizes:
            rows = [r for st in per.values() for r in st["rows"] if r["m"] == m]
            if rows:
                pooled_rows.append({"m": m, "classes": len(rows),
                                    **{k: float(np.mean([r[k] for r in rows])) for k in ("mean_false_alert", "share_above_alpha", "mean_detection")},
                                    "theoretical_share_above_alpha": rows[0]["theoretical_share_above_alpha"]})
        studies[s.name] = {"per_class": per, "pooled": pooled_rows,
                           "note": "Per-class rows are indicative; pooled rows average the classes with equal weight. No interval is reported for these shares."}

    examples_rows = {c: [{"path": r["path"], "label": r["label"], "score": r["scores"]["full"], "flag": r["flags"]["full"]["marginal"]}
                         for r in lines["descriptor"][c]["rows"] if not r["refused"]] for c in classes} if "descriptor" in names else {}
    picked = pick_examples(examples_rows) if examples_rows else {k: None for k in SETS}
    for v in picked.values():
        if v:
            v["jpeg"] = _jpeg(root, v["path"])

    rows = []
    for c in classes:
        first = lines[scorers[0].name][c]["rows"]
        for i, r in enumerate(first):
            rows.append({"path": r["path"], "class": c, "label": r["label"], "refused": r["refused"],
                         "scores": {s.name: lines[s.name][c]["rows"][i]["scores"] for s in scorers},
                         "decisions": {s.name: {key: (lines[s.name][c]["rows"][i]["flags"][config].get(rule) if not r["refused"] else None)
                                                for key, config, rule in RULES} for s in scorers}})
    return {"identity": {"dataset": "VisA", "manifest_digest": digest(manifest), "archive_sha256": manifest["archive_sha256"],
                         "split_sha256": manifest["split_sha256"], "split_commit": manifest.get("split_commit")},
            "settings": PROTOCOL, "quick": quick,
            "notes": [ATTRIBUTION,
                      "Real public images (VisA), not MIZAN's own data. No escape-rate or industrial certificate follows from them.",
                      "The engine path submits each image as the engine itself would see it: greyscale, 96x96, PNG. "
                      "dinov2-native sees the original colour image and is off the engine path.",
                      "Exchangeability: the false-alert promise rests on the test normals behaving like the calibration "
                      "normals; the check compares the observed count with the exact distribution that assumption implies.",
                      "Many intervals are reported; none is adjusted for multiple comparisons."],
            "splits": splits, "per_class": per_class, "pooled": pooled, "exchangeability": exch, "auroc": auc,
            "paired": paired, "study": studies, "examples": picked, "rows": rows}
