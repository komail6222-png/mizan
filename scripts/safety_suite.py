"""Run the deterministic simulation and the metamorphic safety suite; write reports/safety/safety.json.

OPENBLAS_NUM_THREADS=1 python scripts/safety_suite.py            # full run, about a minute
python scripts/safety_suite.py --quick                           # smaller run for a laptop smoke test
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mizan import safety, simulation  # noqa: E402
from mizan.evaluation import environment  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--out", default=ROOT / "reports/safety/safety.json")
    args = parser.parse_args()
    q = args.quick
    out, timing = {"environment": environment(), "quick": q}, {}
    for name, fn in [
        ("simulation", lambda: simulation.explore(range(150 if q else 1000), 40)),
        ("simulation_mutants", lambda: simulation.mutants(range(60 if q else 200), 40)),
        ("sanad", lambda: safety.sanad_suite(150 if q else 600)),
        ("sanad_mutants", lambda: safety.sanad_mutants(100 if q else 200)),
        ("basira", lambda: safety.basira_suite(500 if q else 3000, 5000 if q else 40000)),
        ("rasid", lambda: safety.rasid_suite(40 if q else 200)),
    ]:
        start = time.perf_counter()
        out[name] = fn()
        timing[name] = round(time.perf_counter() - start, 2)
    out["elapsed_seconds"] = timing
    out["summary"] = {
        "simulation_steps": out["simulation"]["steps"], "simulation_violations": out["simulation"]["violations"],
        "simulation_mutants_caught": f"{sum(v['caught'] for v in out['simulation_mutants'].values())}/{len(out['simulation_mutants'])}",
        "sanad_checks": out["sanad"]["total_checks"], "sanad_violations": out["sanad"]["violations"],
        "sanad_mutants_caught": f"{sum(v['caught'] for v in out['sanad_mutants'].values())}/{len(out['sanad_mutants'])}",
        "basira_checks": sum(out["basira"]["checks"].values()), "basira_violations": out["basira"]["violations"],
        "basira_max_abs_z": out["basira"]["max_abs_z"],
        "rasid_plans_verified": f"{out['rasid']['solver_plans_verified']}/{out['rasid']['problems']}",
        "rasid_invalid_mutations_rejected": f"{out['rasid']['mutations_rejected']}/{out['rasid']['mutations_attempted']}",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))
