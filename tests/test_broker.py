import contextlib
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mizan.core.contract import Refused, digest, strict_load
from mizan.core.recovery import restore, validate_backup
from mizan.core.store import Broker, connect


class BrokerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = 10
        self.b = Broker(self.root / "store", clock=lambda: self.now, lease=4, max_queue=3)
        self.addCleanup(self.b.close)
        self.spec = {"engine": "cpu-v1", "policy": "first"}
        self.b.promote("one", "s", self.spec, 0)

    def submit(self, key="demo", value=None):
        return self.b.submit("one", "owner", "s", key, value or {"in": 1})

    def test_expiry_fences_old_and_duplicate_publication(self):
        jid = self.submit()
        old = self.b.claim()
        self.now += 5
        current = self.b.claim()
        self.assertGreater(current["epoch"], old["epoch"])
        with self.assertRaises(Refused):
            self.b.publish(old, {"out": 8})
        self.b.publish(current, {"out": 9})
        with self.assertRaises(Refused):
            self.b.publish(current, {"out": 9})
        self.assertEqual(self.b.get("one", "owner", jid)["result"], {"out": 9})
        actions = [json.loads(r[0])["action"] for r in self.b.db.execute("SELECT body FROM audit")]
        self.assertEqual(actions.count("published"), 1)

    def test_cancellation_in_both_orders(self):
        jid = self.submit()
        token = self.b.claim()
        self.assertEqual(self.b.cancel("one", "owner", jid), "CANCELLED")
        with self.assertRaises(Refused):
            self.b.publish(token, {})
        other = self.submit("next")
        self.b.publish(self.b.claim(), {})
        self.assertEqual(self.b.cancel("one", "owner", other), "SUCCEEDED")

    def test_scoped_operation_and_host_lock(self):
        jid = self.submit()
        self.assertEqual(jid, self.submit())
        with self.assertRaises(Refused):
            self.submit(value={"in": 2})
        self.b.promote("two", "s", self.spec, 0)
        other = self.b.submit("two", "owner", "s", "demo", {"in": 1})
        self.assertNotEqual(jid, other)
        for t, a in [("one", "stranger"), ("two", "owner")]:
            with self.assertRaises(Refused):
                self.b.get(t, a, jid)
        with self.assertRaisesRegex(Refused, "Only one"):
            Broker(self.b.root)

    def test_queue_admission_and_physical_claim_limit(self):
        for i in range(3):
            self.submit(str(i))
        with self.assertRaisesRegex(Refused, "full"):
            self.submit("overflow")
        self.assertIsNotNone(self.b.claim())
        self.assertIsNone(self.b.claim())

    def test_pinned_reader_checkpoint_and_disk_admission(self):
        with contextlib.closing(connect(self.b.root / "state.db")) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM jobs").fetchone()
            self.submit()
            stats = self.b.checkpoint()
            self.assertGreater(stats["frames"], stats["checkpointed"])
            self.b.disk_floor = 10**19
            with self.assertRaisesRegex(Refused, "paused"):
                self.submit("low-disk")
            reader.execute("ROLLBACK")
        self.assertEqual(self.b.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_three_busy_tenants_get_a_turn_before_a_repeat(self):
        self.b.max_queue=20
        for tenant in ("one","two","three"):
            if tenant!="one":
                self.b.promote(tenant,"s",self.spec,0)
            for i in range(3):
                self.b.submit(tenant,"owner","s",str(i),{"i":i})
        sequence=[]
        for i in range(6):
            t=self.b.claim()
            sequence.append(t["tenant"])
            self.b.publish(t,{"out":i})
        self.assertEqual(sequence,["one","two","three"]*2)

    def test_scoped_signature_and_changed_bytes(self):
        blob = self.b.cas.put({"amount": 20})
        seal = self.b.cas.seal("one", "result", blob, "ctx")
        with self.assertRaises(Refused):
            self.b.cas.open(seal, "two", "result", "ctx")
        forged = copy.deepcopy(seal)
        forged["body"]["object"] = self.b.cas.put({"amount": 21})
        with self.assertRaisesRegex(Refused, "signature"):
            self.b.cas.open(forged, "one", "result", "ctx")
        self.b.cas.path(blob).write_text("{}")
        with self.assertRaisesRegex(Refused, "digest"):
            self.b.cas.open(seal, "one", "result", "ctx")

    def test_gc_retains_authoritative_input_and_result(self):
        jid = self.submit()
        token = self.b.claim()
        result = self.b.publish(token, {"out": 2})
        orphan = self.b.cas.put({"orphan": True})
        self.assertIn(orphan, self.b.collect(grace=-1))
        self.assertTrue(self.b.cas.path(token["payload"]).is_file())
        self.assertTrue(self.b.cas.path(result).is_file())
        self.assertEqual(self.b.get("one", "owner", jid)["result"]["out"], 2)

    def test_backup_restore_and_generation_fencing(self):
        job = self.submit()
        self.b.publish(self.b.claim(), {"out": 2})
        pending = self.submit("pending")
        stale = self.b.claim()
        target = self.b.backup(self.root / "snap")
        heads = self.b.verify_audit()
        with self.assertRaises(Refused):
            validate_backup(self.b, target, {"one": (1, "wrong")})
        generation = restore(self.b, target, self.root / "restored", heads)
        self.assertNotEqual(generation, stale["generation"])
        with contextlib.closing(Broker(self.root / "restored")) as rb:
            self.assertEqual(rb.get("one", "owner", job)["result"], {"out": 2})
            self.assertEqual(rb.get("one", "owner", pending)["state"], "QUEUED")
            with self.assertRaises(Refused):
                rb.publish(stale, {"late": True})
        file = next((target / "objects").iterdir())
        file.write_bytes(b"changed")
        with self.assertRaisesRegex(Refused, "artifact"):
            validate_backup(self.b, target, heads)

    def test_revision_compare_and_swap_rollback_revocation(self):
        self.submit()
        job = self.b.claim()
        self.b.promote("one", "s", {"engine": "cpu-v1", "policy": "second"}, 1)
        with self.assertRaises(Refused):
            self.b.publish(job, {})
        with self.assertRaises(Refused):
            self.b.promote("one", "s", self.spec, 1)
        with self.assertRaises(Refused):
            self.b.rollback("one", "s", 1, 2, "other-runtime")
        self.assertEqual(self.b.rollback("one", "s", 1, 2, "cpu-v1"), 3)
        self.b.revoke("one", digest(self.spec))
        with self.assertRaises(Refused):
            self.b.rollback("one", "s", 1, 4, "cpu-v1")
        with self.assertRaises(Refused):
            self.submit("after-revocation")

    def test_mandatory_audit_failure_rolls_back(self):
        jid = self.submit()
        token = self.b.claim()
        log = self.b.log
        def unavailable(*args, **kwargs):
            raise OSError("audit unavailable")
        self.b.log = unavailable
        with self.assertRaises(OSError):
            self.b.publish(token, {"out": 3})
        self.b.log = log
        self.assertEqual(self.b.get("one", "owner", jid)["state"], "RUNNING")
        self.b.publish(token, {"out": 3})

    def test_wire_format_rejects_duplicate_and_nonfinite(self):
        for invalid in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}']:
            with self.assertRaises(Refused):
                strict_load(invalid)


class ProcessCrashTests(unittest.TestCase):
    def test_crash_on_either_side_of_publish_commit(self):
        child = """
from pathlib import Path
import os,sys
from mizan.core.store import Broker
b=Broker(sys.argv[1])
b.promote('t','s',{'engine':'cpu'},0)
j=b.submit('t','user','s','k',{'input':1})
token=b.claim()
log=b.log
def crash(db,t,action,**fields):
    if action=='published' and sys.argv[2]=='before':
        os._exit(31)
    return log(db,t,action,**fields)
b.log=crash
b.publish(token,{'output':17})
os._exit(32)
"""
        for stage, code in [("before", 31), ("after", 32)]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as store:
                run = subprocess.run([sys.executable, "-c", child, store, stage], timeout=10)
                self.assertEqual(run.returncode, code)
                with contextlib.closing(Broker(store)) as b:
                    jid = b.submit("t", "user", "s", "k", {"input": 1})
                    got = b.get("t", "user", jid)
                    if stage == "before":
                        self.assertEqual(got["state"], "QUEUED")
                        self.assertIn(digest({"output": 17}), b.collect(grace=-1))
                        b.publish(b.claim(), {"output": 17})
                    self.assertEqual(b.get("t", "user", jid)["result"]["output"], 17)
                    self.assertTrue(b.verify_audit())
