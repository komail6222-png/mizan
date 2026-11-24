"""Evaluate the pinned Hugging Face models on the held-out protocol (optional runtime, .venv-hf).

    python scripts/evaluate_hf.py --models var/hf --out reports/hf [--only basira|rasid|sanad] [--repeat]

For each system the current component runs through the harness of mizan/hf_evaluation.py and is gated before any
model result is kept: BASIRA's shipped descriptor against reports/evaluation/basira.json, before DINOv2 runs; RASID's
ridge policies against reports/evaluation/rasid.json, from the same run that plans on Chronos-Bolt; SANAD's lexical
retriever across two separate runs. A failed gate exits with code 2. Files are written only after every requested
gate has passed, each through a temporary file and a rename. quick=True (tests only) uses reduced sizes and never
writes. Nothing here imports torch: the model loaders in mizan/models.py do, after verifying the checkpoint.
"""
import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import evaluation, hf_evaluation as hf, models  # noqa: E402  (hf.*_gate is looked up at call time)
from mizan.basira import engine as b  # noqa: E402
from mizan.core.contract import Refused, canonical, require  # noqa: E402

SYSTEMS = ("basira", "rasid", "sanad")
LOADERS = {"basira": "dinov2_features", "rasid": "chronos_forecaster", "sanad": "e5_embedder"}   # in mizan.models
DINOV2_PROFILE = "hf:facebook/dinov2-small@" + models.PINS["basira"]["revision"] + "/224-imagenet-v1"
FULL_SIZES = {"basira": {"draws": 400, "sizes": (24, 50, 100), "pools": (4000, 4000, 400)},
              "rasid": {"days": 60}, "sanad": {"n_docs": 120, "seed": 2024}}
QUICK_SIZES = {"basira": {"draws": 3, "sizes": (24,), "pools": (60, 60, 20)},
               "rasid": {"days": 2}, "sanad": {"n_docs": 10, "seed": 2024}}
SANAD_PROTOCOL_SIZE = (120, 2024)   # (n_docs, seed): the only SANAD results that are written
SHIPPED = {"basira": "reports/evaluation/basira.json", "rasid": "reports/evaluation/rasid.json"}

DETERMINISM = {"torch_seed": 0, "deterministic_algorithms": "torch.use_deterministic_algorithms(True, warn_only=True)",
               "torch_threads": 8, "forward_passes": "torch.inference_mode()",
               "applied_by": "mizan.models._torch, called by every loader after the checkpoint is verified"}
IDENTITY_NOTE = ("manifest_sha256 is the SHA-256 of this machine's signed import (mizan-manifest.json, whose seal carries "
                 "a local signature), while manifest_object is core.contract.digest of the signed manifest object (the pin "
                 "and each file's SHA-256), reproducible by anyone re-importing the pin.")

BASIRA_GATE_RULE = ("Exact equality with reports/evaluation/basira.json, checked before the model runs: the frozen test's "
                    "detections, false alerts and bank digest; the seed, condition, p-value and flag of each item; the "
                    "study settings and bank digest; every shipped-calibration field and every field of every calibration row.")
RASID_GATE_RULE = ("The ridge side of the same run against reports/evaluation/rasid.json: the day count, each origin's date and "
                   "ridge 80% coverage and each policy's verified-plan count exactly; the mean and per-origin ridge and "
                   f"seasonal-naive load MAE within {hf.RASID_MAE_TOLERANCE:g} kW; the candidate and candidate_risk_neutral "
                   f"totals within {hf.RASID_CANDIDATE_TOLERANCE:g} relative (the shipped file was solved with another SciPy); "
                   f"the other policies' totals within {hf.RASID_BASELINE_TOLERANCE:g} relative.")
SANAD_GATE_RULE = ("Two separate sanad_hf calls with the same embedder: the same retrieval-set digest and query count, the "
                   "lexical ranker mizan.sanad.engine.retrieve in both, and all 27 lexical metrics (3 query kinds x 3 "
                   "languages x 3 metrics) exactly equal. The E5 side is not gated; the first call's result is kept.")

BASIRA_NOTES = ["Baseline and model are scored in this run on the same 80 frozen items and the same calibration pools, "
                "seeds and draws; the baseline is the shipped descriptor, which reproduces reports/evaluation/basira.json "
                "exactly (gate)."]
RASID_NOTES = ["The ridge view (baseline) and the Chronos view (model) come from one rasid_hf run: the same origins, actual "
               "hours, planner settings and verifier, each policy carrying its own battery state and month-to-date peak.",
               "Paired comparisons use only this run's costs; the gate compares the ridge totals with the shipped file "
               "(gate.total_cost_relative_difference), not single days."]
SANAD_NOTES = ["The bilingual query is near its ceiling for the lexical retriever because the grammar only accepts lines "
               "beginning with the exact attribute label and the engine's bilingual query is exactly those labels, so the "
               "English-only and Arabic-only queries are the discriminating comparison.",
               "Verdicts are not recomputed from either ranking: the grammar keeps verdict authority."]
QUERIES_UNIT = "(document, attribute) pairs, each ranked with three query kinds"

RIDGE_ORIGIN = ("date", "ridge_mae", "naive_mae", "coverage_80_load", "ridge_mean_pinball", "ridge_truncated_crps")
CHRONOS_ORIGIN = ("date", "chronos_mae", "chronos_coverage_80_load", "chronos_mean_pinball", "chronos_truncated_crps",
                  "context_hours", "pv_values_clipped")
RIDGE_FORECAST = ("ridge_mae_kw", "seasonal_naive_mae_kw", "shared_levels", "ridge_mean_pinball", "ridge_truncated_crps",
                  "ridge_coverage_80_load")
CHRONOS_FORECAST = ("chronos_mae_kw", "shared_levels", "chronos_mean_pinball", "chronos_truncated_crps",
                    "chronos_coverage_80_load", "pv_values_clipped")
CHRONOS_POLICIES = tuple(hf.PLANNED_AS)
RASID_KEYS = {"target_days", "origins", "forecast", "decision_quality", "paired_comparisons", "scenario_rule"}
SANAD_SHARED = ("set_digest", "documents", "queries", "queries_by_language")
SANAD_KEYS = {"n_docs", "seed", "lexical_ranker", "metrics", "protocol", *SANAD_SHARED}


def _say(message):
    print(time.strftime("%H:%M:%S"), message, file=sys.stderr, flush=True)


def _quiet(message):
    pass


def _version(dist):
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _cpu():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or None


def environment():
    """evaluation.environment() plus the optional runtime's versions and the torch state the loaders left behind."""
    torch = sys.modules.get("torch")
    return {**evaluation.environment(), "torch": _version("torch"), "transformers": _version("transformers"),
            "chronos_forecasting": _version("chronos-forecasting"), "tokenizers": _version("tokenizers"),
            "pillow": _version("pillow"), "torch_threads": torch.get_num_threads() if torch else None,
            "torch_deterministic_algorithms": torch.are_deterministic_algorithms_enabled() if torch else None,
            "torch_deterministic_warn_only": torch.is_deterministic_algorithms_warn_only_enabled() if torch else None,
            "thread_variables": {k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")},
            "cpu": _cpu()}


def _shipped(kind):
    return json.loads((ROOT / SHIPPED[kind]).read_text())


def _gate(kind, result, against, rule, say):
    """The gate record, or exit with code 2 (nothing has been written yet) when any check differs."""
    if not result["passed"]:
        names = result["mismatches"]
        say(f"{kind}: gate failed, {len(names)} of {len(result['checked'])} checks differ: "
            + ", ".join(names[:20]) + (", ..." if len(names) > 20 else ""))
        raise SystemExit(2)
    say(f"{kind}: gate passed ({len(result['checked'])} checks against {against})")
    return {**result, "against": against, "rule": rule}


def _basira(root, load, size, say):
    say("basira: baseline, the shipped descriptor through the harness")
    baseline = hf.basira_hf(b.features, b.DESCRIPTOR, **size)
    gate = _gate("basira", hf.basira_gate(baseline, _shipped("basira")), SHIPPED["basira"], BASIRA_GATE_RULE, say)
    say("basira: model, DINOv2 patch features")
    model = hf.basira_hf(load(root), DINOV2_PROFILE, **size)
    pools = size["pools"]
    settings = {"baseline_profile": b.DESCRIPTOR, "model_profile": DINOV2_PROFILE,
                "model_features": ("RGB, bicubic resize to 224 x 224, ImageNet mean and standard deviation, last hidden "
                                   "state without the CLS token, each patch L2-normalised: a 16 x 16 x 384 grid in float64, "
                                   "one image per forward pass"),
                "frozen_test": "80 items, seeds 9100-9179: index below 40 intact, otherwise pit if the index is odd, else crack",
                "pools": {"calibration_pool_normals": f"{pools[0]} intact parts, seeds from 30000",
                          "evaluation_normals": f"{pools[1]} intact parts, seeds from 40000",
                          "evaluation_defects": f"{pools[2]} parts, seeds from 50000, pit if the seed is odd, else crack"},
                "draws": size["draws"], "sizes": list(size["sizes"]), "alpha": str(hf.BASIRA_ALPHA),
                "delta": str(hf.BASIRA_DELTA), "study_seed": hf.BASIRA_SEED,
                "patch_rule": ("basira.engine.score averages the highest 2.5% of patch scores, rounded up; "
                               "patches_averaged in baseline and model is that count for each grid"),
                "determinism": DETERMINISM}
    return {"gate": gate, "baseline": baseline, "model": model, "settings": settings, "notes": BASIRA_NOTES}


def rasid_views(result):
    """Split one rasid_hf result into the ridge view (the shipped policies: everything rasid_gate reads) and the
    Chronos view (its forecast scores, its two policies and the paired comparisons). Each origin and forecast field
    goes to one view (date and shared_levels to both); a field that belongs to neither stops the split (SPLIT)."""
    def costs(policies):
        return tuple(p + suffix for p in policies for suffix in ("_cost", "_status"))
    ridge, chronos = RIDGE_ORIGIN + costs(evaluation.POLICIES), CHRONOS_ORIGIN + costs(CHRONOS_POLICIES)
    forecast = result["forecast"]
    require(set(result) == RASID_KEYS, "SPLIT", "A RASID result key belongs to neither view")
    require(all(set(row) == set(ridge) | set(chronos) for row in result["origins"]), "SPLIT", "An origin field belongs to neither view")
    require(set(forecast) == set(RIDGE_FORECAST) | set(CHRONOS_FORECAST) | {"note"}, "SPLIT", "A forecast field belongs to neither view")
    require(set(result["decision_quality"]) == set(evaluation.POLICIES) | set(CHRONOS_POLICIES), "SPLIT", "Unexpected policies")
    def view(fields, forecast_fields, policies):
        return {"target_days": result["target_days"], "origins": [{k: row[k] for k in fields} for row in result["origins"]],
                "forecast": {k: forecast[k] for k in forecast_fields},
                "decision_quality": {k: result["decision_quality"][k] for k in policies}}
    return (view(ridge, RIDGE_FORECAST, evaluation.POLICIES),
            {**view(chronos, CHRONOS_FORECAST, CHRONOS_POLICIES), "paired_comparisons": result["paired_comparisons"]})


def _rasid(root, load, size, say):
    say("rasid: the ridge and Chronos-Bolt policies in one run")
    result = hf.rasid_hf(load(root), **size)
    shipped = _shipped("rasid")
    gate = _gate("rasid", hf.rasid_gate(result, shipped), SHIPPED["rasid"], RASID_GATE_RULE, say)
    got, want = result["decision_quality"], shipped["decision_quality"]
    gate["total_cost_relative_difference"] = {k: (got[k]["total_cost"] - want[k]["total_cost"]) / want[k]["total_cost"]
                                              for k in evaluation.POLICIES}
    baseline, model = rasid_views(result)
    settings = {"origins": result["target_days"], "first_origin": result["origins"][0]["date"],
                "ridge_forecast": "rasid.forecast.forecast(rows, origin, grid, 4), as shipped",
                "chronos_levels": list(hf.CHRONOS_LEVELS), "shared_levels": list(hf.SHARED_LEVELS),
                "scenario_weights": [1 / len(hf.CHRONOS_LEVELS)] * len(hf.CHRONOS_LEVELS),
                "pairing": "load quantile path j with solar quantile path 4 - j, j = 0 to 4",
                "chronos_context": ("the contiguous run of hours known at the origin (vintage rule), ending the hour before "
                                    "it; the loader passes at most its last 2048 values for each series"),
                "quantile_repair": ("Chronos quantiles are sorted across levels at each hour (monotone rearrangement), in the "
                                    "loader and again in the harness, and solar values below zero are clipped at zero before "
                                    "scoring and planning (counted in pv_values_clipped); load is not clipped"),
                "scenario_rule": result["scenario_rule"], "forecast_note": result["forecast"]["note"],
                "comparisons": [list(c) for c in hf.RASID_COMPARISONS],
                "gate_tolerances": {"mae_kw_absolute": hf.RASID_MAE_TOLERANCE,
                                    "candidate_totals_relative": hf.RASID_CANDIDATE_TOLERANCE,
                                    "other_totals_relative": hf.RASID_BASELINE_TOLERANCE},
                "determinism": DETERMINISM}
    return {"gate": gate, "baseline": baseline, "model": model, "settings": settings, "notes": RASID_NOTES}


def sanad_views(result):
    """The lexical half (baseline) and the E5 half (model) of one sanad_hf result, each with the shared set fields."""
    require(set(result) == SANAD_KEYS and set(result["metrics"]) == {"lexical", "e5"}, "SPLIT",
            "A SANAD result key belongs to neither view")
    shared = {**{k: result[k] for k in SANAD_SHARED}, "queries_unit": QUERIES_UNIT}
    return ({"retriever": "lexical", "ranker": result["lexical_ranker"], **shared, "metrics": result["metrics"]["lexical"]},
            {"retriever": "e5", **shared, "metrics": result["metrics"]["e5"]})


def _sanad(root, load, size, say):
    embed = load(root)
    say("sanad: first run")
    first = hf.sanad_hf(embed, **size)
    say("sanad: second run")
    second = hf.sanad_hf(embed, **size)
    gate = _gate("sanad", hf.sanad_gate(first, second), "a second sanad_hf call in this process", SANAD_GATE_RULE, say)
    baseline, model = sanad_views(first)
    settings = {"n_docs": first["n_docs"], "seed": first["seed"], "queries": QUERIES_UNIT,
                "query_kinds": list(hf.QUERY_KINDS), "languages": list(hf.RETRIEVAL_LANGUAGES),
                "metrics": list(hf.RETRIEVAL_METRICS), "lexical_ranker": hf.LEXICAL_RANKER,
                "embedding": ("multilingual E5: 'query: ' and 'passage: ' prefixes, one padded batch per call, truncation at "
                              "256 tokens, mean pooling over the attention mask, L2-normalised; ranked by cosine, ties in "
                              "document order"),
                "protocol": first["protocol"], "determinism": DETERMINISM}
    return {"gate": gate, "baseline": baseline, "model": model, "settings": settings, "notes": SANAD_NOTES}


_ABSENT = object()


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def differences(first, second, path=""):
    """Every leaf at which two JSON values differ: its path, both values and, for two numbers, |first - second|.
    A value present on one side only is named with absent_in; equal values of different types differ."""
    def at(key):
        return f"{path}.{key}" if path else str(key)
    if isinstance(first, dict) and isinstance(second, dict):
        return [d for k in sorted(set(first) | set(second)) for d in differences(first.get(k, _ABSENT), second.get(k, _ABSENT), at(k))]
    if isinstance(first, list) and isinstance(second, list):
        item = lambda xs, i: xs[i] if i < len(xs) else _ABSENT
        return [d for i in range(max(len(first), len(second))) for d in differences(item(first, i), item(second, i), f"{path}[{i}]")]
    if type(first) is type(second) and first == second:
        return []
    row = {"path": path, "first": None if first is _ABSENT else first, "second": None if second is _ABSENT else second}
    if first is _ABSENT or second is _ABSENT:
        row["absent_in"] = "first" if first is _ABSENT else "second"
    elif _number(first) and _number(second):
        row["abs_difference"] = abs(first - second)
    return [row]


def _evaluate(kind, root, load, size, say):
    start = time.perf_counter()
    record = {"basira": _basira, "rasid": _rasid, "sanad": _sanad}[kind](root, load, size, say)
    record["identity"] = {**models.model_identity(kind, root), "note": IDENTITY_NOTE}
    record["environment"] = environment()
    record["fixture_only"] = True
    record["elapsed_seconds"] = round(time.perf_counter() - start, 4)
    say(f"{kind}: done in {record['elapsed_seconds']:.1f} s")
    return record


def _compared(record):
    return json.loads(canonical({k: record[k] for k in ("gate", "baseline", "model", "identity")}))


def _write(out, records):
    """Write each record to out/<kind>.json: every file is staged first, then each is renamed into place."""
    require(all(r["gate"]["passed"] for r in records.values()), "GATE", "A record without a passed gate is never written")
    if "sanad" in records:
        s = records["sanad"]["settings"]
        require((s["n_docs"], s["seed"]) == SANAD_PROTOCOL_SIZE, "PROTOCOL_SIZE",
                "SANAD results are written only for the protocol's 120 documents and seed 2024")
    data = {kind: canonical(r) for kind, r in records.items()}
    out = Path(out); out.mkdir(parents=True, exist_ok=True); staged = []
    try:
        for kind, blob in data.items():
            fd, name = tempfile.mkstemp(dir=out, prefix=f".{kind}.", suffix=".tmp"); staged.append((name, out / f"{kind}.json"))
            with os.fdopen(fd, "wb") as f:
                f.write(blob); f.flush(); os.fsync(f.fileno())
        for name, path in staged:
            os.replace(name, path)
    finally:
        for name, _ in staged:
            Path(name).unlink(missing_ok=True)
    return [path for _, path in staged]


def run(models_root, out, only=None, repeat=False, *, loaders=None, quick=False) -> dict:
    """Evaluate the requested systems (all three by default); with repeat, run each again and record the comparison.
    loaders maps a system to a function of models_root returning its feature function, forecaster or embedder
    (default: the verified loaders of mizan.models). Returns the records; writes them unless quick."""
    require(only is None or only in SYSTEMS, "ONLY", "Expected basira, rasid or sanad")
    say = _quiet if quick else _say
    sizes = QUICK_SIZES if quick else FULL_SIZES
    records = {}
    for kind in SYSTEMS if only is None else (only,):
        load = (loaders or {}).get(kind) or getattr(models, LOADERS[kind])
        records[kind] = _evaluate(kind, models_root, load, sizes[kind], say)
        if repeat:
            say(f"{kind}: repeat run")
            again = _evaluate(kind, models_root, load, sizes[kind], say)
            diff = differences(_compared(records[kind]), _compared(again))
            numeric = [d["abs_difference"] for d in diff if "abs_difference" in d]
            records[kind]["repeat_run"] = {
                "identical": not diff, "compared": "gate, baseline, model and identity",
                "method": "the whole evaluation of this system again in the same process, the model loaded again",
                "differing_values": len(diff), "max_abs_difference": max(numeric) if numeric else None,
                "differences": diff, "elapsed_seconds": again["elapsed_seconds"]}
            say(f"{kind}: repeat " + ("identical" if not diff else f"differs in {len(diff)} values"))
    if not quick:
        for path in _write(out, records):
            say(f"wrote {path}")
    return records


def summary(records):
    """One line per system with the headline numbers, for the terminal."""
    lines = []
    for kind, r in records.items():
        base, model = r["baseline"], r["model"]
        if kind == "basira":
            b0, m0 = base["frozen_test"], model["frozen_test"]
            b1, m1 = (x["calibration_study"]["shipped_calibration"]["long_run_false_alert_estimate"] for x in (base, model))
            text = (f"descriptor {b0['detected']}/40 detected, {b0['false_alerts']}/40 false alerts, long-run false alert {b1:.4f}; "
                    f"DINOv2 {m0['detected']}/40, {m0['false_alerts']}/40, {m1:.4f}")
        elif kind == "rasid":
            f, g = base["forecast"], model["forecast"]
            pairs = "; ".join(f"{c['policy']} - {c['reference']} {c['mean_daily_difference']:+.3f} "
                              f"[{c['mean_daily_difference_95ci'][0]:+.3f}, {c['mean_daily_difference_95ci'][1]:+.3f}]"
                              for c in model["paired_comparisons"])
            text = (f"load MAE ridge {f['ridge_mae_kw']:.3f}, seasonal naive {f['seasonal_naive_mae_kw']:.3f}, "
                    f"Chronos {g['chronos_mae_kw']:.3f} kW; mean daily cost difference {pairs}")
        else:
            r1 = lambda half, query: half["metrics"][query]["all"]["recall_at_1"]
            text = "recall@1 lexical/E5: " + ", ".join(f"{q} {r1(base, q):.4f}/{r1(model, q):.4f}" for q in hf.QUERY_KINDS)
        repeat = r.get("repeat_run")
        lines.append(f"{kind}: gate passed; {text}" + ("" if repeat is None else f"; repeat identical: {repeat['identical']}"))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--models", default="var/hf", help="verified checkpoint cache (python -m mizan hf fetch)")
    p.add_argument("--out", default="reports/hf")
    p.add_argument("--only", choices=SYSTEMS)
    p.add_argument("--repeat", action="store_true", help="run each system twice and record whether the results are identical")
    args = p.parse_args(argv)
    try:
        records = run(args.models, args.out, args.only, args.repeat)
    except Refused as exc:
        print(f"Refused {exc.code}: {exc}", file=sys.stderr)
        return 1
    print(summary(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
