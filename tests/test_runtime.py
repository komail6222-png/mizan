"""Release 2.2 runtime regressions: runner survival, shutdown semantics, O(1) result lookup, schema migration."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from mizan.core.contract import Refused
from mizan.core.store import SCHEMA_VERSION, Broker
from mizan.service import Runner


class RunnerResilienceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.b = Broker(Path(temp.name) / "store")
        self.addCleanup(lambda: self.b.close())  # tests may replace self.b with a reopened broker
        self.b.promote("t", "s", {"engine": "fixture"}, 0)

    def test_unexpected_exception_fails_job_and_loop_survives(self):
        first = self.b.submit("t", "u", "s", "a", {"n": 1})
        second = self.b.submit("t", "u", "s", "b", {"n": 2})
        runner = Runner(self.b)
        calls = []

        def execute(token):
            calls.append(token["id"])
            if len(calls) == 1:
                raise AttributeError("worker output was not an object")
            return {"ok": token["id"]}

        runner.execute = execute
        with contextlib.redirect_stderr(io.StringIO()) as log:
            runner.start()
            until = time.monotonic() + 10
            while self.b.get("t", "u", second)["state"] != "SUCCEEDED" and time.monotonic() < until:
                time.sleep(.02)
            runner.close()
        self.assertEqual(self.b.get("t", "u", first)["state"], "FAILED")
        self.assertEqual(self.b.get("t", "u", first)["reason"], "WORKER_ERROR")
        self.assertEqual(self.b.get("t", "u", second)["result"], {"ok": second})
        self.assertIn('"error": "AttributeError"', log.getvalue())

    def test_broker_fault_outside_a_job_backs_off_without_killing_thread(self):
        runner = Runner(self.b)
        failures = []

        def broken_claim():
            failures.append(1)
            raise OSError("transient store fault")

        self.b.claim = broken_claim
        with contextlib.redirect_stderr(io.StringIO()):
            runner.start()
            time.sleep(.6)
            health = runner.health()
            runner.close()
        self.assertTrue(health["alive"])
        self.assertGreaterEqual(health["consecutive_errors"], 1)
        self.assertEqual(health["last_error"], "OSError")
        self.assertLess(len(failures), 10, "loop must back off instead of spinning")

    def test_shutdown_leaves_attempt_for_restart_fencing(self):
        job = self.b.submit("t", "u", "s", "a", {"n": 1})
        runner = Runner(self.b)

        def stopping(token):
            raise Refused("SHUTDOWN", "Runner is stopping")

        runner.execute = stopping
        self.assertTrue(runner.one())
        self.assertEqual(self.b.get("t", "u", job)["state"], "RUNNING")
        root = self.b.root
        self.b.close()
        with contextlib.closing(Broker(root)) as reopened:
            got = reopened.get("t", "u", job)
            self.assertEqual((got["state"], got["reason"]), ("QUEUED", "RESTART_FENCED"))
        self.b = Broker(root)  # keep cleanup symmetric

    def test_retry_budget_exhaustion_is_named(self):
        now = [0]
        root = self.b.root
        self.b.close()
        self.b = Broker(root, clock=lambda: now[0], lease=1)
        job = self.b.submit("t", "u", "s", "a", {"n": 1})
        for _ in range(3):
            self.assertIsNotNone(self.b.claim())
            now[0] += 2
        self.assertIsNone(self.b.claim())
        got = self.b.get("t", "u", job)
        self.assertEqual((got["state"], got["reason"]), ("FAILED", "RETRY_BUDGET_EXHAUSTED"))


class ResultLookupTests(unittest.TestCase):
    def test_lookup_is_independent_of_audit_length_and_carries_provenance(self):
        with tempfile.TemporaryDirectory() as d, contextlib.closing(Broker(d, max_queue=10**6)) as b:
            b.promote("t", "s", {"engine": "fixture"}, 0)
            oldest = None
            for n in range(400):
                j = b.submit("t", "u", "s", str(n), {"n": n})
                b.publish(b.claim(), {"o": n})
                oldest = oldest or j
            # The audit scan is now only a legacy fallback: make it unusable and lookups must still work.
            b._result_generation = lambda *a: (_ for _ in ()).throw(AssertionError("audit scan used"))
            got = b.get("t", "u", oldest)
            self.assertEqual(got["result"], {"o": 0})
            self.assertEqual(got["provenance"]["generation"], b.generation)
            self.assertEqual(got["provenance"]["key_fingerprint"], b.cas.key_id)

    def test_store_from_release_2_1_is_migrated_and_old_results_still_open(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.closing(Broker(d)) as b:
                b.promote("t", "s", {"engine": "fixture"}, 0)
                job = b.submit("t", "u", "s", "k", {"n": 1})
                b.publish(b.claim(), {"o": 1})
                # Recreate the 2.1 layout: no generation column and no schema_version row.
                with b.tx() as db:
                    db.execute("ALTER TABLE jobs DROP COLUMN generation")
                    db.execute("DELETE FROM meta WHERE k='schema_version'")
            with contextlib.closing(Broker(d)) as b:
                columns = {r["name"] for r in b.db.execute("PRAGMA table_info(jobs)")}
                self.assertIn("generation", columns)
                self.assertEqual(b.db.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(b.get("t", "u", job)["result"], {"o": 1})  # legacy row uses the audit fallback
                actions = [json.loads(r[0])["action"] for r in b.db.execute("SELECT body FROM audit WHERE tenant='system'")]
                self.assertEqual(actions.count("schema_migrated"), 1)
                self.assertTrue(b.verify_audit())
            with contextlib.closing(Broker(d)) as b:  # idempotent: no second migration event
                actions = [json.loads(r[0])["action"] for r in b.db.execute("SELECT body FROM audit WHERE tenant='system'")]
                self.assertEqual(actions.count("schema_migrated"), 1)


if __name__ == "__main__":
    unittest.main()
