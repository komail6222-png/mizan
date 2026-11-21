"""Deterministic simulation testing of the real broker.

Seeded schedules drive the actual `Broker` (SQLite, CAS, signatures) through interleavings that are hard to reach by
hand: two workers racing, lease expiry, heartbeats, cancellation, deployment changes, process crashes that leave
zombie workers holding stale tokens. After every step the harness checks invariants against an independent reference
model of the publication predicate. A seed reproduces its schedule exactly.

`mutants` re-runs the same schedules against deliberately broken brokers (fencing checks removed). A harness that
cannot catch those bugs proves nothing, so the report records whether each mutant was caught.
"""
import contextlib
import json
import random
import shutil
import tempfile
from pathlib import Path

from .core.contract import Refused
from .core.store import RETRY_BUDGET, Broker

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
LEASE = 4


class Violation(AssertionError):
    pass


class Simulation:
    def __init__(self, seed, steps=30, broker_class=Broker, max_jobs=4):
        self.rng = random.Random(seed)
        self.seed, self.steps, self.broker_class, self.max_jobs = seed, steps, broker_class, max_jobs
        self.dir = Path(tempfile.mkdtemp(prefix="mizan-sim-"))
        self.now = 100.0
        self.tokens = {"w1": None, "w2": None}
        self.jobs = []                  # job ids in submission order
        self.published = {}             # job -> (epoch, value) accepted by the broker
        self.terminal = {}              # job -> terminal state first observed
        self.epochs = {}
        self.trace = []
        self.stats = {"steps": 0, "crashes": 0, "publishes": 0, "stale_publishes_refused": 0, "cancels": 0, "promotions": 0}
        self.open()
        self.broker.promote("t", "s", {"engine": "sim", "v": 0}, 0)
        self.revision = 1

    def open(self):
        self.broker = self.broker_class(self.dir / "store", clock=lambda: self.now, lease=LEASE, max_queue=16, max_running=2)

    def close(self):
        with contextlib.suppress(Exception):
            self.broker.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------ reference model ---
    def row(self, job):
        return self.broker.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def should_accept(self, token):
        """Independent statement of the fenced publication predicate (architecture §3.3)."""
        r = self.row(token["id"])
        d = self.broker.db.execute("SELECT * FROM deployments WHERE tenant='t' AND subject='s'").fetchone()
        return (token["generation"] == self.broker.generation and r["state"] == "RUNNING" and r["epoch"] == token["epoch"]
                and r["cancel"] == token["cancel"] and r["expires"] > self.now and d["state"] == "READY" and d["rev"] == token["rev"])

    # ------------------------------------------------------------------- actions ---
    def act(self):
        b, rng = self.broker, self.rng
        choices = ["tick", "claim", "publish", "heartbeat", "fail", "cancel", "submit", "crash", "promote"]
        weights = [3, 5, 5, 2, 1, 1, 2, 0.6, 0.3]
        action = rng.choices(choices, weights)[0]
        worker = rng.choice(sorted(self.tokens))
        token = self.tokens[worker]
        if action == "tick":
            self.now += rng.choice([1, 2, 3, 5])
        elif action == "submit" and len(self.jobs) < self.max_jobs:
            try:
                self.jobs.append(b.submit("t", "u", "s", f"job-{len(self.jobs)}", {"n": len(self.jobs)}))
            except Refused as exc:
                if exc.code not in ("NOT_READY", "QUEUE_FULL"):
                    raise
        elif action == "claim" and token is None:
            self.tokens[worker] = b.claim()
        elif action == "publish" and token is not None:
            expected = self.should_accept(token)
            value = {"by": worker, "epoch": token["epoch"], "seed": self.seed}
            try:
                b.publish(token, value)
                accepted = True
            except Refused:
                accepted = False
            if accepted != expected:
                raise Violation(f"publish accepted={accepted} but reference model says {expected}")
            if accepted:
                if token["id"] in self.published:
                    raise Violation("second accepted publication for one job")
                self.published[token["id"]] = (token["epoch"], value)
                self.stats["publishes"] += 1
            else:
                self.stats["stale_publishes_refused"] += 1
            self.tokens[worker] = None
        elif action == "heartbeat" and token is not None:
            expected = self.row(token["id"])["state"] == "RUNNING" and self.should_accept_lease(token)
            try:
                b.heartbeat(token)
                ok = True
            except Refused:
                ok = False
            if ok != expected:
                raise Violation(f"heartbeat accepted={ok} but reference model says {expected}")
        elif action == "fail" and token is not None:
            with contextlib.suppress(Refused):
                b.fail(token, "SIMULATED")
            self.tokens[worker] = None
        elif action == "cancel" and self.jobs:
            b.cancel("t", "u", rng.choice(self.jobs))
            self.stats["cancels"] += 1
        elif action == "crash":
            # Workers keep their tokens: after restart they are zombies that must be fenced.
            self.broker.close()
            self.open()
            self.stats["crashes"] += 1
            if self.broker.db.execute("SELECT count(*) FROM jobs WHERE state='RUNNING'").fetchone()[0]:
                raise Violation("restart left a RUNNING job unfenced")
        elif action == "promote":
            self.revision = b.promote("t", "s", {"engine": "sim", "v": self.revision}, self.revision)
            self.stats["promotions"] += 1
        else:
            action = "noop"
        self.trace.append(f"{action}:{worker}")
        return action

    def should_accept_lease(self, token):
        r = self.row(token["id"])
        return (token["generation"] == self.broker.generation and r["epoch"] == token["epoch"]
                and r["cancel"] == token["cancel"] and r["expires"] > self.now)

    # ---------------------------------------------------------------- invariants ---
    def check(self):
        b = self.broker
        b.verify_audit()
        events = [json.loads(r[0]) for r in b.db.execute("SELECT body FROM audit WHERE tenant='t'")]
        published = [e["fields"]["job"] for e in events if e["action"] == "published"]
        if len(published) != len(set(published)):
            raise Violation("a job has two publication events")
        for job in self.jobs:
            r = self.row(job)
            if r["epoch"] < self.epochs.get(job, 0):
                raise Violation("attempt epoch moved backwards")
            self.epochs[job] = r["epoch"]
            if job in self.terminal and r["state"] != self.terminal[job]:
                raise Violation(f"terminal state changed {self.terminal[job]} -> {r['state']}")
            if r["state"] in TERMINAL:
                self.terminal.setdefault(job, r["state"])
            if r["state"] == "SUCCEEDED":
                got = b.get("t", "u", job)   # opens and verifies the signed receipt
                if job not in self.published or got["result"] != self.published[job][1]:
                    raise Violation("stored result is not the accepted proposal")
            elif job in self.published:
                raise Violation("an accepted publication is not SUCCEEDED")
            if r["state"] == "FAILED" and r["reason"] == "RETRY_BUDGET_EXHAUSTED" and r["epoch"] < RETRY_BUDGET:
                raise Violation("retry budget exhausted too early")

    def run(self):
        try:
            for _ in range(self.steps):
                self.act()
                self.stats["steps"] += 1
                self.check()
            return {"seed": self.seed, "ok": True, **self.stats}
        except Violation as exc:
            return {"seed": self.seed, "ok": False, "violation": str(exc), "trace": self.trace[-12:], **self.stats}
        finally:
            self.close()


def explore(seeds, steps=30, broker_class=Broker):
    runs = [Simulation(seed, steps, broker_class).run() for seed in seeds]
    total = {k: sum(r[k] for r in runs) for k in ("steps", "crashes", "publishes", "stale_publishes_refused", "cancels", "promotions")}
    failures = [r for r in runs if not r["ok"]]
    return {"schedules": len(runs), "steps_per_schedule": steps, **total, "violations": len(failures), "failures": failures[:5]}


# ------------------------------------------------------------------ mutants ---
class NoEpochFence(Broker):
    """Mutant: publication forgets to compare the attempt epoch."""
    def _fence(self, db, t):
        r = db.execute("SELECT * FROM jobs WHERE id=? AND tenant=?", (t["id"], t["tenant"])).fetchone()
        if not (r and r["state"] == "RUNNING" and r["expires"] > self.clock()):
            raise Refused("STALE_ATTEMPT", "mutant")
        return r


class NoCancelFence(Broker):
    """Mutant: publication ignores cancellation and lets late results overwrite a terminal state."""
    def _fence(self, db, t):
        r = db.execute("SELECT * FROM jobs WHERE id=? AND tenant=?", (t["id"], t["tenant"])).fetchone()
        if not (r and r["epoch"] == t["epoch"] and r["expires"] > self.clock() and t["generation"] == self.generation):
            raise Refused("STALE_ATTEMPT", "mutant")
        return r


MUTANTS = {"publication without epoch fence": NoEpochFence, "publication without cancellation fence": NoCancelFence}


def mutants(seeds, steps=30):
    out = {}
    for name, cls in MUTANTS.items():
        report = explore(seeds, steps, cls)
        out[name] = {"caught": report["violations"] > 0, "schedules_with_violation": report["violations"],
                     "example": report["failures"][0]["violation"] if report["failures"] else None}
    return out
