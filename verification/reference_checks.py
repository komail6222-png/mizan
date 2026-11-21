"""Executable checks for MIZAN Architecture v2.0.

Run: python3 reference_checks.py [results.json]
Standard library only. All databases are temporary and all examples synthetic.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import platform
import sqlite3
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path


def rank_index(m: int, alpha: Fraction) -> int:
    x = (m + 1) * (1 - alpha)
    return (x.numerator + x.denominator - 1) // x.denominator


def weighted_cvar(costs, probabilities, beta):
    assert 0 <= beta < 1
    assert len(costs) == len(probabilities) > 0
    assert all(math.isfinite(x) for x in costs)
    assert all(math.isfinite(p) and p > 0 for p in probabilities)
    assert math.isclose(math.fsum(probabilities), 1.0, abs_tol=1e-12)
    return min(
        z + math.fsum(p * max(c - z, 0) for p, c in zip(probabilities, costs)) / (1 - beta)
        for z in sorted(set(costs))
    )


def check_calibration():
    assert rank_index(18, Fraction(1, 20)) == 19
    assert rank_index(19, Fraction(1, 20)) == 19
    assert rank_index(4, Fraction(1, 20)) == 5
    return {"m18_k": 19, "m19_k": 19, "holdout_m4_k": 5,
            "minimum_nontrivial_p_for_m4": 0.2}


def check_defect_evidence():
    upper3 = 1 - 0.05 ** (1 / 3)
    upper299 = 1 - 0.05 ** (1 / 299)
    upper298 = 1 - 0.05 ** (1 / 298)
    assert upper3 > 0.63
    assert upper299 < 0.01 < upper298
    return {"zero_misses_n3_upper_95": upper3,
            "zero_misses_n299_upper_95": upper299,
            "assumptions": "fixed decision rule; independent representative defect trials"}


def check_loo_dominance():
    total, pvalues = 0, []
    for points in itertools.product(range(4), repeat=4):
        train, test = points[:3], points[3]
        old = [min(abs(x-y) for j,y in enumerate(train) if i != j)
               for i,x in enumerate(train)]
        augmented = [min(abs(x-y) for j,y in enumerate(points) if i != j)
                     for i,x in enumerate(points)]
        test_score = min(abs(test-y) for y in train)
        assert augmented[-1] == test_score
        assert all(a >= b for a,b in zip(old, augmented[:3]))
        p_loo = Fraction(1 + sum(s >= test_score for s in old), 4)
        p_full = Fraction(sum(s >= test_score for s in augmented), 4)
        assert p_loo >= p_full
        total += 1
        pvalues.append(p_loo)
    for alpha in [Fraction(1,4), Fraction(1,2), Fraction(3,4)]:
        assert Fraction(sum(p <= alpha for p in pvalues), total) <= alpha
    return {"enumerated_exchangeable_tuples": total,
            "scope": "scalar nearest-neighbor example; not a proof for arbitrary pipelines"}


def check_risk_objective():
    p, beta, kappa = [0.5,0.5], 0.9, 0.5
    a, b = [0.0,100.0], [60.0,60.0]
    ea = math.fsum(c*w for c,w in zip(a,p))
    eb = math.fsum(c*w for c,w in zip(b,p))
    ca, cb = weighted_cvar(a,p,beta), weighted_cvar(b,p,beta)
    ja, jb = (1-kappa)*ea+kappa*ca, (1-kappa)*eb+kappa*cb
    assert (ea,eb,ca,cb,ja,jb) == (50,60,100,60,75,60)
    assert jb < ja and eb > ea and ja != ea
    # The upper quarter includes all of the 10% high-cost case and 15%
    # of the 20% middle-cost case. The remaining probability is outside it.
    fractional_tail = weighted_cvar([-20, 10, 100], [0.7, 0.2, 0.1], 0.75)
    assert math.isclose(fractional_tail, 46)
    negative_tail = weighted_cvar([-10, -3], [0.5, 0.5], 0.75)
    assert math.isclose(negative_tail, -3)
    return {"A": {"expected":ea,"cvar":ca,"objective":ja},
            "B": {"expected":eb,"cvar":cb,"objective":jb},
            "fractional_tail_cvar": fractional_tail, "negative_cost_cvar": negative_tail}


def check_peak_counterexample():
    def candidate(x):
        grid = [1+x,1-x]
        cost = 0*grid[0] + 10*grid[1] + max(grid)
        return cost, max(grid), grid
    points = [(i/100, *candidate(i/100)) for i in range(101)]
    best = min(points,key=lambda row:row[1])
    assert candidate(0) == (11,1,[1,1])
    assert best == (1.0,2.0,2.0,[2.0,0.0])
    return {"baseline_cost":11,"baseline_peak":1,"optimal_cost":2,
            "optimal_peak":2,"analytic_objective":"11 - 9*x, 0 <= x <= 1"}


def check_unequal_quantile_quadrature():
    tau, q, y = [0.05,0.10,0.50,0.90,0.95], [-10,-4,8,20,30], 0
    loss = [t*(y-v) if y>=v else (1-t)*(v-y) for t,v in zip(tau,q)]
    mean_proxy = 2*math.fsum(loss)/len(loss)
    truncated = 2*math.fsum((tau[i+1]-tau[i])*(loss[i]+loss[i+1])/2
                             for i in range(len(tau)-1))
    assert math.isclose(mean_proxy, 3.36)
    assert math.isclose(truncated, 4.38)
    assert not math.isclose(mean_proxy * (tau[-1] - tau[0]), truncated)
    return {"uniform_mean_proxy":mean_proxy,"trapezoid_on_0p05_to_0p95":truncated,
            "tail_integrals":"unspecified; neither is asserted to be full CRPS"}


def check_sqlite_fencing():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory)/"check.db")
        a = sqlite3.connect(path,isolation_level=None)
        b = sqlite3.connect(path,isolation_level=None)
        try:
            assert a.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            for c in (a,b):
                c.execute("PRAGMA synchronous=FULL")
                c.execute("PRAGMA foreign_keys=ON")
            a.executescript("""
            CREATE TABLE job(id INTEGER PRIMARY KEY, epoch INTEGER, state TEXT, cancelled INTEGER);
            CREATE TABLE result(job INTEGER UNIQUE REFERENCES job(id), value TEXT);
            CREATE TABLE audit(seq INTEGER PRIMARY KEY, event TEXT);
            CREATE TABLE pointer(subject TEXT PRIMARY KEY, revision INTEGER, artifact TEXT);
            INSERT INTO job VALUES(1,1,'RUNNING',0);
            INSERT INTO pointer VALUES('basira',1,'old');
            """)
            b.execute("UPDATE job SET epoch=2 WHERE id=1 AND epoch=1")
            a.execute("BEGIN IMMEDIATE")
            stale = a.execute("UPDATE job SET state='SUCCEEDED' WHERE id=1 AND epoch=1 AND state='RUNNING' AND cancelled=0").rowcount
            a.execute("ROLLBACK")
            assert stale == 0
            b.execute("UPDATE job SET cancelled=1 WHERE id=1")
            cancelled = a.execute("UPDATE job SET state='SUCCEEDED' WHERE id=1 AND epoch=2 AND state='RUNNING' AND cancelled=0").rowcount
            assert cancelled == 0
            a.execute("INSERT INTO job VALUES(2,3,'RUNNING',0)")
            a.execute("BEGIN IMMEDIATE")
            publish = "UPDATE job SET state='SUCCEEDED' WHERE id=2 AND epoch=3 AND state='RUNNING' AND cancelled=0"
            assert a.execute(publish).rowcount == 1
            a.execute("INSERT INTO result VALUES(2, 'verified')")
            a.execute("COMMIT")
            assert b.execute(publish).rowcount == 0
            assert b.execute("SELECT count(*) FROM result WHERE job=2").fetchone()[0] == 1
            a.execute("BEGIN IMMEDIATE")
            assert a.execute("UPDATE pointer SET artifact='new',revision=2 WHERE subject='basira' AND revision=1").rowcount == 1
            a.execute("INSERT INTO audit VALUES(1,'promotion')")
            a.execute("ROLLBACK")
            assert a.execute("SELECT revision,artifact FROM pointer").fetchone() == (1,'old')
            assert a.execute("SELECT count(*) FROM audit").fetchone()[0] == 0
            a.execute("BEGIN IMMEDIATE")
            assert a.execute("UPDATE pointer SET artifact='new',revision=2 WHERE subject='basira' AND revision=1").rowcount == 1
            a.execute("INSERT INTO audit VALUES(1,'promotion')")
            a.execute("COMMIT")
            assert b.execute("UPDATE pointer SET artifact='other',revision=3 WHERE subject='basira' AND revision=1").rowcount == 0
            assert b.execute("SELECT count(*) FROM audit").fetchone()[0] == 1
            assert a.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            return {"stale_worker_rows":stale,"cancelled_worker_rows":cancelled,
                    "aborted_promotion_atomic":True,"stale_promotion_rejected":True,
                    "accepted_result_rows_after_duplicate":1,
                    "scope":"sequential interleavings, not a concurrent load or crash test"}
        finally:
            a.close(); b.close()


def check_wal_crash_and_backup():
    """Kill the process without cleanup, on either side of the same commit."""
    writer = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE pointer SET revision = 1')
db.execute('INSERT INTO audit VALUES(1)')
if sys.argv[2] == 'after_commit':
    db.execute('COMMIT')
os._exit(29)
"""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "control.db"
        db = sqlite3.connect(path, isolation_level=None)
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        db.executescript(
            "CREATE TABLE pointer(revision INTEGER); INSERT INTO pointer VALUES(0);"
            "CREATE TABLE audit(seq INTEGER PRIMARY KEY);"
        )
        db.close()
        states = {}
        for phase, expected in [("before_commit", (0, 0)), ("after_commit", (1, 1))]:
            child = subprocess.run([sys.executable, "-c", writer, str(path), phase],
                                   capture_output=True, text=True, timeout=15)
            assert child.returncode == 29, child.stderr
            recovered = sqlite3.connect(path)
            try:
                assert recovered.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                state = recovered.execute("SELECT revision,(SELECT COUNT(*) FROM audit) FROM pointer").fetchone()
                assert state == expected
                states[phase] = {"revision": state[0], "audit_rows":state[1]}
                if phase == "after_commit":
                    backup = sqlite3.connect(Path(directory) / "restored.db")
                    try:
                        recovered.backup(backup)
                        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                        assert backup.execute("SELECT revision,(SELECT COUNT(*) FROM audit) FROM pointer").fetchone() == expected
                    finally:
                        backup.close()
            finally:
                recovered.close()
        return {"reopened_database":states, "verified_online_backup":True,
                "scope":"abrupt process exit on local storage, excluding power loss and disk destruction"}


def main():
    checks = {}
    for fn in [check_calibration,check_defect_evidence,check_loo_dominance,
               check_risk_objective,check_peak_counterexample,
               check_unequal_quantile_quadrature,check_sqlite_fencing,check_wal_crash_and_backup]:
        checks[fn.__name__] = {"status":"PASS", "evidence":fn()}
    payload = {"scope":"Reference mathematics and small SQLite transaction checks only.",
               "python":platform.python_version(),"sqlite":sqlite3.sqlite_version,
               "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "checks":checks,"all_passed":True}
    text = json.dumps(payload,indent=2,sort_keys=True)+"\n"
    if len(sys.argv)>1:
        Path(sys.argv[1]).write_text(text,encoding="utf-8")
    print(text)


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("Run with assertions enabled (omit -O).")
    main()
