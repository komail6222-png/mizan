"""Evaluate BASIRA on real VisA images under the sealed protocol (optional runtime, .venv-hf for the DINOv2 scorers).

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MIZAN_HF_MODELS=var/hf .venv-hf/bin/python scripts/evaluate_visa.py \\
        --data var/visa --models var/hf --out reports/visa --repeat --seal <seal commit> --reason "<why>"

That is the command for the published evidence. --out is required, and reports/visa is written only with --repeat:
the whole evaluation then runs a second time in a fresh Python process and the record says whether the two are
identical. Run scripts/fetch_visa.py first; this script never touches the network and refuses any image whose hash
differs from reports/visa/manifest.json.

Before any VisA image is scored, a real run checks that mizan.visa.PROTOCOL still has its sealed digest and that
--seal names the commit that sealed it, then runs the fidelity gate: the shipped descriptor must reproduce the
frozen test of reports/evaluation/basira.json exactly, and DINOv2 must reproduce 3.1's frozen test of
reports/hf/basira.json (decisions exact, scores within 1e-6 relative). A failed gate exits with code 2 and writes
nothing. --dry-run scores train normals only, for timing and memory, and writes no evidence. Every run that scores
VisA images, dry or real, is appended to reports/visa/runs.json.
"""
import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import visa  # noqa: E402
from mizan import visa_evaluation as ve  # noqa: E402
from mizan.core.contract import Refused, canonical, digest, require  # noqa: E402
from scripts.evaluate_hf import _write, differences, environment  # noqa: E402
from scripts.fetch_visa import data_root  # noqa: E402

EVIDENCE_DIR = ROOT / "reports" / "visa"
MANIFEST = EVIDENCE_DIR / "manifest.json"
RUN_LOG = EVIDENCE_DIR / "runs.json"
HF_SHIPPED = ROOT / "reports" / "hf" / "basira.json"
VOLATILE = ("elapsed_seconds", "environment", "repeat_run", "provenance")


def default_loaders(models_root):
    """The three scorers: the shipped descriptor and the pinned DINOv2-small, on the engine path and at native input.
    Both DINOv2 scorers share one verified model; their profile strings name the checkpoint and the input."""
    from mizan import models
    features = models.dinov2_features(models_root)
    pin = models.PINS["basira"]
    profile = f"hf:{pin['repo']}@{pin['revision']}/224-imagenet-v1"
    return {"descriptor": ve.DESCRIPTOR,
            "dinov2-engine": ve.Scorer("dinov2-engine", features, True, profile),
            "dinov2-native": ve.Scorer("dinov2-native", features, False, profile + "/native-rgb")}


def _git(*args):
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def check_seal(seal):
    """--seal must be a commit on this branch's history whose mizan/visa.py carries the current SEALED_DIGEST."""
    require(_git("cat-file", "-e", f"{seal}^{{commit}}") is not None, "VISA_SEAL", f"{seal} is not a commit here")
    require(_git("merge-base", "--is-ancestor", seal, "HEAD") is not None, "VISA_SEAL", f"{seal} is not an ancestor of HEAD")
    sealed = _git("show", f"{seal}:mizan/visa.py") or ""
    require(f'SEALED_DIGEST = "{visa.SEALED_DIGEST}"' in sealed, "VISA_SEAL",
            f"{seal} does not seal the protocol digest {visa.SEALED_DIGEST}")


def _peak_rss_mib():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _append_log(log, entry):
    log = Path(log)
    entries = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    entries.append(entry)
    log.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=log.parent, prefix=".runs.", suffix=".tmp")
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(entries, indent=1, sort_keys=True) + "\n")
    os.replace(name, log)


def _git_state():
    """The code a run starts from: HEAD and whether any tracked file differs from it (outputs are untracked)."""
    return {"commit": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain", "--untracked-files=no"))}


def _log_entry(*, state, dry_run, reason, seal, out, started, extra=None):
    return {"commit": state["commit"], "dirty": state["dirty"],
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dry_run": dry_run, "reason": reason,
            "protocol_digest": digest(visa.PROTOCOL), "seal_commit": seal,
            "evidence_dir": Path(out).resolve() == EVIDENCE_DIR.resolve(),
            "elapsed_seconds": round(time.perf_counter() - started, 1), "peak_rss_mib": _peak_rss_mib(), **(extra or {})}


def _gates(scorers, say):
    fixture = ve.fixture_gate()
    shipped = json.loads(HF_SHIPPED.read_text(encoding="utf-8"))["model"]["frozen_test"]
    dinov2 = ve.dinov2_gate(ve.frozen_record(scorers["dinov2-engine"]), shipped)
    for name, g in (("fixture", fixture), ("dinov2", dinov2)):
        if not g["passed"]:
            names = [m["name"] for m in g["mismatches"]]
            say(f"{name} gate failed: {len(names)} of {len(g['checked'])} checks differ: " + ", ".join(names[:20]))
            raise SystemExit(2)
    say(f"gates passed: fixture {len(fixture['checked'])} checks exact; dinov2 {len(dinov2['checked'])} checks, "
        f"bit-exact {dinov2['bit_exact']}")
    return {"passed": True, "fixture": fixture, "dinov2": dinov2}


def _dry_run(root, manifest, scorers, quick, say):
    """Time each scorer on the train normals only: bank, calibration and both pools. No test image is opened."""
    sizes = ve.QUICK["split"] if quick else None
    shipped = ve.QUICK["as_shipped"] if quick else (10, 24)
    timings = {s: 0.0 for s in scorers}
    images = 0
    for cls in [c for c in visa.CLASSES if any(x["object"] == c for x in manifest["images"])]:
        sp = visa.protocol_split(root, manifest, cls, sizes=sizes)
        configs = {"as_shipped": {"reference": sp["reference"][:shipped[0]], "calibration": sp["calibration"][:shipped[1]],
                                  "rules": ["marginal"]},
                   "full": {"reference": sp["reference"], "calibration": sp["calibration"], "rules": ["marginal", "pac"]}}
        extra = [(p, "draw") for p in sp["draw_pool"]] + [(p, "eval") for p in sp["eval_pool"]]
        images += len(sp["reference"]) + len(sp["calibration"]) + len(extra)
        for name, scorer in scorers.items():
            start = time.perf_counter()
            ve.product_line(root, configs, extra, scorer)
            timings[name] += time.perf_counter() - start
        say(f"  {cls}: done ({_peak_rss_mib()} MiB peak)")
    return {"train_images_per_scorer": images, "seconds_per_scorer": {k: round(v, 1) for k, v in timings.items()}}


def _record(root, manifest, scorers, models_root, injected, quick, gate, seal):
    from mizan import models
    record = ve.evaluate_visa(root, manifest, list(scorers.values()), quick=quick)
    record["identity"]["descriptor"] = ve.DESCRIPTOR.profile
    record["identity"]["dinov2"] = ({"profile": scorers["dinov2-engine"].profile, "note": "injected scorers"} if injected
                                    else {**models.model_identity("basira", models_root), "profile": scorers["dinov2-engine"].profile})
    record.update(gate=gate, protocol_digest=digest(visa.PROTOCOL), seal_commit=seal)
    return record


def _compared(record):
    return json.loads(canonical({k: v for k, v in record.items() if k not in VOLATILE}))


def _second_process(data, models_root, seal, manifest_path, quick):
    """The repeat run: this script again, in a fresh interpreter, writing to a temporary folder and no log."""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--data", str(data), "--models", str(models_root),
               "--out", tmp, "--reason", "repeat run (second process)", "--seal", seal, "--manifest", str(manifest_path),
               "--child"] + (["--quick"] if quick else [])
        subprocess.run(cmd, check=True, env=os.environ.copy())
        return json.loads((Path(tmp) / "basira.json").read_text(encoding="utf-8"))


def run(data, out, *, models_root, reason, repeat=False, dry_run=False, seal=None, loaders=None, quick=False,
        log=RUN_LOG, manifest_path=MANIFEST, child=False):
    started = time.perf_counter()
    state = _git_state()
    say = lambda s: print(s, flush=True)  # noqa: E731
    require(repeat or child or Path(out).resolve() != EVIDENCE_DIR.resolve(), "EVIDENCE_OUT",
            f"{EVIDENCE_DIR} holds the published evidence: write it only with --repeat, or choose another --out")
    if not dry_run:
        require(bool(seal), "VISA_SEAL", "A real run names the seal commit (--seal)")
        require(digest(visa.PROTOCOL) == visa.SEALED_DIGEST, "VISA_PROTOCOL",
                "mizan.visa.PROTOCOL differs from its sealed digest; a changed protocol is a new, separately labelled analysis")
        check_seal(seal)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    root = data_root(Path(data) / "data", [{"image": x["path"]} for x in manifest["images"]])
    train_only = {**manifest, "images": [x for x in manifest["images"] if x["split"] == "train"]}
    visa.verify_manifest(root, train_only if dry_run else manifest)
    say(f"verified {len((train_only if dry_run else manifest)['images']):,} images against the manifest")
    scorers = (loaders or default_loaders)(models_root)

    if dry_run:
        timings = _dry_run(root, train_only, scorers, quick, say)
        say(json.dumps(timings))
        if not child:
            _append_log(log, _log_entry(state=state, dry_run=True, reason=reason, seal=seal, out=out, started=started, extra=timings))
        return {"dry_run": True, **timings}

    gate = _gates(scorers, say)
    record = _record(root, manifest, scorers, models_root, loaders is not None, quick, gate, seal)
    if repeat and not child:
        if loaders is None:
            second, process = _second_process(data, models_root, seal, manifest_path, quick), "a second Python process"
        else:
            second, process = _record(root, manifest, scorers, models_root, True, quick, gate, seal), "the same process (injected scorers)"
        diff = differences(_compared(record), _compared(second))
        record["repeat_run"] = {"identical": not diff, "process": process, "differing_values": len(diff), "differences": diff[:50]}
    record["environment"] = {**environment(), "peak_rss_mib": _peak_rss_mib()}
    record["provenance"] = {**state, "note": "HEAD and tracked-file state when the run started"}
    record["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    paths = _write(out, {"basira": record})
    say(f"wrote {', '.join(str(p) for p in paths)}")
    if not child:
        _append_log(log, _log_entry(state=state, dry_run=False, reason=reason, seal=seal, out=out, started=started,
                                    extra={"repeat_identical": record.get("repeat_run", {}).get("identical")}))
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     epilog="Published evidence: --out reports/visa --repeat --seal <commit>.")
    parser.add_argument("--data", required=True, type=Path, help="the folder scripts/fetch_visa.py wrote, e.g. var/visa")
    parser.add_argument("--models", default=os.environ.get("MIZAN_HF_MODELS", "var/hf"), type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reason", required=True, help="why this run happens; recorded in reports/visa/runs.json")
    parser.add_argument("--seal", help="the commit that sealed mizan.visa.PROTOCOL (required for a real run)")
    parser.add_argument("--repeat", action="store_true", help="run everything again in a second process and compare")
    parser.add_argument("--dry-run", action="store_true", help="train normals only: timing and memory, no evidence")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--quick", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        run(args.data, args.out, models_root=args.models, reason=args.reason, repeat=args.repeat, dry_run=args.dry_run,
            seal=args.seal, quick=args.quick, manifest_path=args.manifest, child=args.child)
    except Refused as exc:
        print(f"Refused {exc.code}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
