"""Evaluate RASID on two real business sites from the OPSD household package, under the sealed protocol.

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MIZAN_HF_MODELS=var/hf .venv-hf/bin/python scripts/evaluate_opsd.py \\
        --data var/opsd --models var/hf --out reports/opsd --repeat --seal <seal commit> --reason "<why>"

That is the command for the published evidence (.venv-hf: Chronos needs torch). --out is required, and reports/opsd
is written only with --repeat: the whole evaluation then runs a second time in a fresh Python process and the record
says whether the two are identical. Run scripts/fetch_opsd.py first; this script never touches the network and
refuses a data file whose size or SHA-256 is not the pinned one.

Before any site day is forecast, a run checks that mizan.opsd.PROTOCOL still has its sealed digest and that --seal
names the commit that introduced it, verifies the file, and runs the generated-site gate: the day loop must reproduce
reports/evaluation/rasid.json and the Chronos side of reports/hf/rasid.json. A failed gate exits with code 2 and
writes nothing. Every run that forecasts site days is appended to reports/opsd/runs.json: a "started" entry before the
first site day and a "finished" entry once the record is written. --child is internal: only --repeat starts it.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import opsd, visa  # noqa: E402
from mizan import opsd_evaluation as oe  # noqa: E402
from mizan.core.contract import Refused, canonical, digest, require  # noqa: E402
from scripts.evaluate_hf import _write, differences, environment  # noqa: E402
from scripts.evaluate_visa import _append_log, _git, _git_state, _peak_rss_mib  # noqa: E402

EVIDENCE_DIR = ROOT / "reports" / "opsd"
RUN_LOG = EVIDENCE_DIR / "runs.json"
VOLATILE = ("elapsed_seconds", "environment", "repeat_run", "provenance")
CHILD_ENV = "MIZAN_OPSD_REPEAT_OUT"


def check_seal(seal):
    """--seal must be the commit on this branch's history that introduced the current SEALED_DIGEST: its
    mizan/opsd.py carries the digest and its parent's does not."""
    require(bool(seal), "OPSD_SEAL", "A run names the seal commit (--seal)")
    require(_git("cat-file", "-e", f"{seal}^{{commit}}") is not None, "OPSD_SEAL", f"{seal} is not a commit here")
    require(_git("merge-base", "--is-ancestor", seal, "HEAD") is not None, "OPSD_SEAL", f"{seal} is not an ancestor of HEAD")
    line = f'SEALED_DIGEST = "{opsd.SEALED_DIGEST}"'
    require(line in (_git("show", f"{seal}:mizan/opsd.py") or ""), "OPSD_SEAL",
            f"{seal} does not seal the protocol digest {opsd.SEALED_DIGEST}")
    require(line not in (_git("show", f"{seal}^:mizan/opsd.py") or ""), "OPSD_SEAL",
            f"{seal} only inherits the protocol digest; the seal is the commit that introduced it")


def _log_entry(*, event, state, reason, seal, out, started, extra=None):
    entry = {"event": event, "commit": state["commit"], "dirty": state["dirty"],
             "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": reason,
             "protocol_digest": digest(opsd.PROTOCOL), "seal_commit": seal,
             "evidence_dir": Path(out).resolve() == EVIDENCE_DIR.resolve()}
    if event == "finished":
        entry.update(elapsed_seconds=round(time.perf_counter() - started, 1), peak_rss_mib=_peak_rss_mib())
    return {**entry, **(extra or {})}


def _compared(record):
    return json.loads(canonical({k: v for k, v in record.items() if k not in VOLATILE}))


def _second_process(data, models_root, seal, quick):
    """The repeat run: this script again, in a fresh interpreter, writing only to the temporary folder its parent
    names through CHILD_ENV, and logging nothing; the parent's log entries cover it."""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--data", str(data), "--models", str(models_root),
               "--out", tmp, "--reason", "repeat run (second process)", "--seal", seal, "--child"] + (["--quick"] if quick else [])
        subprocess.run(cmd, check=True, env={**os.environ, CHILD_ENV: str(Path(tmp).resolve())})
        return json.loads((Path(tmp) / "rasid.json").read_text(encoding="utf-8"))


def _verified_file(data, expected):
    path = Path(data) / opsd.FILE_NAME
    size, sha = expected
    require(path.is_file() and path.stat().st_size == size, "OPSD_HASH", f"{path} is missing or not {size:,} bytes")
    got = visa.sha256_file(path)
    require(got == sha, "OPSD_HASH", f"{path}: SHA-256 {got}, expected {sha}")
    return path


def run(data, out, *, models_root, reason, repeat=False, seal=None, forecaster=None, quick=False, log=RUN_LOG,
        child=False, expected=(opsd.FILE_BYTES, opsd.FILE_SHA256)):
    started = time.perf_counter()
    state = _git_state()
    say = lambda s: print(s, flush=True)  # noqa: E731
    target = Path(out).resolve()
    if child:
        require(os.environ.get(CHILD_ENV) == str(target) and target != EVIDENCE_DIR.resolve(), "OPSD_CHILD",
                "--child is the repeat run that --repeat starts: its parent names the temporary folder it writes and logs the run")
    require(repeat or target != EVIDENCE_DIR.resolve(), "EVIDENCE_OUT",
            f"{EVIDENCE_DIR} holds the published evidence: write it only with --repeat, or choose another --out")
    require(bool(seal), "OPSD_SEAL", "A run names the seal commit (--seal)")
    require(digest(opsd.PROTOCOL) == opsd.SEALED_DIGEST, "OPSD_PROTOCOL",
            "mizan.opsd.PROTOCOL differs from its sealed digest; a changed protocol is a new, separately labelled analysis")
    check_seal(seal)
    path = _verified_file(data, expected)
    say(f"verified {path} ({expected[0]:,} bytes, SHA-256)")
    injected = forecaster is not None
    if forecaster is None:
        from mizan import models
        forecaster = models.chronos_forecaster(models_root)
    gate = oe.generated_gate(forecaster)
    if not gate["passed"]:
        say(f"generated-site gate failed: {gate['ridge']['mismatches'][:10]} {gate['chronos']['mismatches'][:10]}")
        raise SystemExit(2)
    say(f"gate passed: ridge {len(gate['ridge']['checked'])} checks, Chronos {len(gate['chronos']['checked'])} checks")

    def log_event(event, extra=None):
        if not child:
            _append_log(log, _log_entry(event=event, state=state, reason=reason, seal=seal, out=out, started=started, extra=extra))

    log_event("started")
    record = _record(path, forecaster, models_root, injected, quick, gate, seal)
    if repeat and not child:
        if injected:
            second, process = _record(path, forecaster, models_root, True, quick, gate, seal), "the same process (injected forecaster)"
        else:
            second, process = _second_process(data, models_root, seal, quick), "a second Python process"
        diff = differences(_compared(record), _compared(second))
        record["repeat_run"] = {"identical": not diff, "process": process, "differing_values": len(diff), "differences": diff[:50]}
    record["environment"] = {**environment(), "peak_rss_mib": _peak_rss_mib()}
    record["provenance"] = {**state, "note": "HEAD and tracked-file state when the run started"}
    record["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    paths = _write(out, {"rasid": record})
    say(f"wrote {', '.join(str(p) for p in paths)}")
    log_event("finished", {"repeat_identical": record.get("repeat_run", {}).get("identical")})
    return record


def _record(path, forecaster, models_root, injected, quick, gate, seal):
    record = oe.evaluate_opsd(path, forecaster, quick=quick)
    if injected:
        record["identity"]["chronos"] = {"note": "injected forecaster"}
    else:
        from mizan import models
        record["identity"]["chronos"] = models.model_identity("rasid", models_root)
    record.update(gate=gate, protocol_digest=digest(opsd.PROTOCOL), seal_commit=seal)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     epilog="Published evidence: --out reports/opsd --repeat --seal <commit>.")
    parser.add_argument("--data", required=True, type=Path, help="the folder scripts/fetch_opsd.py wrote, e.g. var/opsd")
    parser.add_argument("--models", default=os.environ.get("MIZAN_HF_MODELS", "var/hf"), type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reason", required=True, help="why this run happens; recorded in reports/opsd/runs.json")
    parser.add_argument("--seal", help="the commit that sealed mizan.opsd.PROTOCOL")
    parser.add_argument("--repeat", action="store_true", help="run everything again in a second process and compare")
    parser.add_argument("--quick", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        run(args.data, args.out, models_root=args.models, reason=args.reason, repeat=args.repeat, seal=args.seal,
            quick=args.quick, child=args.child)
    except Refused as exc:
        print(f"Refused {exc.code}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
