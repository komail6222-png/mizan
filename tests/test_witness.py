"""Audit witness protocol (acceptance case A03), exercised as a same-host simulation."""
import contextlib
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mizan.core.anchoring import anchor, witness_report
from mizan.core.contract import Refused, digest
from mizan.core.recovery import restore
from mizan.core.store import Broker
from mizan.core.witness import Witness, entry_hash, verify_receipt

ROOT = Path(__file__).resolve().parents[1]


def activity(broker, tenant="t", n=2, prefix="job"):
    for i in range(n):
        broker.submit(tenant, "u", "s", f"{prefix}-{i}", {"n": i})
        broker.publish(broker.claim(), {"o": i})


def rewrite_history(broker, tenant, seq, field, value):
    """What a local administrator with full database access can do: edit an event and re-hash the chain after it."""
    with broker.tx() as db:
        db.execute("DROP TRIGGER audit_no_update")
        rows = db.execute("SELECT * FROM audit WHERE tenant=? AND seq>=? ORDER BY seq", (tenant, seq)).fetchall()
        prev = rows[0]["prev"]
        for i, row in enumerate(rows):
            body = json.loads(row["body"])
            if i == 0:
                body["fields"][field] = value
            h = digest(["mizan.audit.v1", tenant, row["seq"], prev, body])
            db.execute("UPDATE audit SET prev=?,body=?,hash=? WHERE tenant=? AND seq=?",
                       (prev, json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")), h, tenant, row["seq"]))
            prev = h
    broker.verify_audit()  # the local chain is self-consistent again: only the witness can tell


class WitnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.b = Broker(self.root / "store")
        self.w = Witness(self.root / "witness")
        self.addCleanup(lambda: self.b.close())
        self.addCleanup(self.w.close)
        self.b.promote("t", "s", {"engine": "fixture"}, 0)
        activity(self.b)

    def statuses(self, report):
        return {t: r["status"] for t, r in report["tenants"].items()}

    def test_anchor_report_tail_and_reanchor(self):
        self.assertEqual(self.statuses(witness_report(self.b, self.w)), {"t": "UNANCHORED_TAIL"})
        receipt_count = anchor(self.b, self.w)["tenants"]["t"]["sent"]
        self.assertEqual(receipt_count, self.b.verify_audit()["t"][0])
        self.assertEqual(self.statuses(witness_report(self.b, self.w)), {"t": "WITNESSED"})
        activity(self.b, prefix="later")
        report = witness_report(self.b, self.w)["tenants"]["t"]
        self.assertEqual((report["status"], report["unanchored_entries"]), ("UNANCHORED_TAIL", 6))
        self.assertGreaterEqual(report["oldest_unanchored_age_s"], 0)
        self.assertEqual(anchor(self.b, self.w)["tenants"]["t"]["sent"], 6)
        self.assertEqual(anchor(self.b, self.w)["tenants"]["t"]["sent"], 0)  # idempotent

    def test_rewritten_local_history_is_detected(self):
        anchor(self.b, self.w)
        rewrite_history(self.b, "t", 3, "kind", "forged")
        report = witness_report(self.b, self.w)
        self.assertFalse(report["consistent"])
        self.assertEqual(self.statuses(report), {"t": "DIVERGED"})
        with self.assertRaisesRegex(Refused, "differs"):
            anchor(self.b, self.w)

    def test_old_copy_of_the_store_is_a_regression(self):
        self.b.close()
        shutil.copytree(self.root / "store", self.root / "old-copy")
        self.b = Broker(self.root / "store")
        activity(self.b, prefix="after-copy")
        anchor(self.b, self.w)
        self.b.close()
        shutil.rmtree(self.root / "store")
        shutil.move(self.root / "old-copy", self.root / "store")
        self.b = Broker(self.root / "store")
        self.assertEqual(self.statuses(witness_report(self.b, self.w)), {"t": "REGRESSED"})
        with self.assertRaises(Refused) as caught:
            anchor(self.b, self.w)
        self.assertEqual(caught.exception.code, "HISTORY_REGRESSED")

    def test_witness_refuses_malformed_forked_and_foreign_batches(self):
        entries = self.b.audit_entries("t")
        store, gen = self.b.store_id, self.b.generation
        forged = copy.deepcopy(entries)
        forged[1]["body"]["fields"]["kind"] = "forged"
        forged[1]["hash"] = entry_hash("t", forged[1])  # self-consistent entry, but entry 3 no longer links to it
        foreign = copy.deepcopy(entries)
        foreign[0]["body"]["generation"] = "someone-else"
        foreign[0]["hash"] = entry_hash("t", foreign[0])
        cases = {"WITNESS_GAP": entries[1:], "WITNESS_FORK": forged, "WITNESS_GENERATION": foreign,
                 "WITNESS_HASH": [dict(entries[0], hash="0" * 64)]}
        for code, batch in cases.items():
            with self.subTest(code=code), self.assertRaises(Refused) as caught:
                self.w.checkpoint(store, gen, "t", batch)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.w.head(store, gen, "t"), (0, "0" * 64))  # nothing partial was accepted
        refused = self.w.db.execute("SELECT count(*) FROM decisions WHERE outcome!='accepted'").fetchone()[0]
        self.assertEqual(refused, len(cases))

    def test_restore_records_recovery_gap_and_fences_the_old_generation(self):
        backup = self.b.backup(self.root / "backup")
        activity(self.b, prefix="lost")             # happens after the backup ...
        anchor(self.b, self.w)                      # ... and is witnessed
        witnessed = self.b.verify_audit()["t"][0]
        restore(self.b, backup, self.root / "restored", self._backup_heads(backup))
        with contextlib.closing(Broker(self.root / "restored")) as rb:
            self.assertEqual(rb.store_id, self.b.store_id)
            result = anchor(rb, self.w)
            gap = [g for g in result["recovery_gaps"] if g["tenant"] == "t"][0]
            self.assertEqual((gap["lost_from"], gap["lost_to"]), (json.loads((backup / "backup.json").read_text())["meta"]["heads"]["t"][0] + 1, witnessed))
            report = witness_report(rb, self.w)
            self.assertTrue(report["consistent"])
            self.assertEqual(report["recovery_gaps"], result["recovery_gaps"])
        activity(self.b, prefix="late")             # the displaced store keeps writing
        with self.assertRaises(Refused) as caught:
            anchor(self.b, self.w)
        self.assertEqual(caught.exception.code, "WITNESS_SUPERSEDED")
        restore(self.b, backup, self.root / "second", self._backup_heads(backup))
        with contextlib.closing(Broker(self.root / "second")) as second, self.assertRaises(Refused) as caught:
            anchor(second, self.w)
        self.assertEqual(caught.exception.code, "WITNESS_SUPERSEDED")

    def test_restore_of_a_backup_ahead_of_the_witness_needs_and_checks_proofs(self):
        anchor(self.b, self.w)
        activity(self.b, prefix="unanchored")      # never sent to the witness
        backup = self.b.backup(self.root / "backup")
        restore(self.b, backup, self.root / "restored")
        with contextlib.closing(Broker(self.root / "restored")) as rb:
            result = anchor(rb, self.w)
            self.assertEqual([g for g in result["recovery_gaps"] if g["tenant"] == "t"], [])
            self.assertEqual(self.statuses(witness_report(rb, self.w))["t"], "WITNESSED")

    def test_offline_receipts_and_domain_separation(self):
        anchor(self.b, self.w)
        report = witness_report(self.b, public_b64=self.w.public_b64)
        self.assertEqual((report["source"], self.statuses(report)), ("stored receipts", {"t": "WITNESSED"}))
        row = self.b.db.execute("SELECT receipt FROM witness_receipts WHERE tenant='t'").fetchone()[0]
        receipt = json.loads(row)
        verify_receipt(receipt, self.w.public_b64, {"tenant": "t", "store": self.b.store_id})
        receipt["body"]["seq"] += 1
        with self.assertRaises(Refused):
            verify_receipt(receipt, self.w.public_b64)
        with contextlib.closing(Witness(self.root / "store" / "nested-witness")) as inside, self.assertRaises(Refused):
            anchor(self.b, inside)

    def _backup_heads(self, backup):
        heads = json.loads((backup / "backup.json").read_text())["meta"]["heads"]
        return {t: tuple(v) for t, v in heads.items()}


class WitnessCliTests(unittest.TestCase):
    def test_init_anchor_and_audit(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            def cli(*args):
                run = subprocess.run([sys.executable, "-m", "mizan", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, 0, run.stderr)
                return json.loads(run.stdout)
            key = cli("witness-init", f"{d}/witness")["public_key_b64"]
            cli("--data", f"{d}/store", "run", "rasid", "examples/rasid_oracle.json")
            self.assertEqual(cli("--data", f"{d}/store", "anchor", "--witness", f"{d}/witness")["tenants"]["local"]["sent"], 6)
            live = cli("--data", f"{d}/store", "audit", "--witness", f"{d}/witness")["witness"]
            offline = cli("--data", f"{d}/store", "audit", "--witness-key", key)["witness"]
            self.assertEqual(live["tenants"]["local"]["status"], "WITNESSED")
            self.assertEqual(offline["tenants"]["local"]["status"], "WITNESSED")
            # Older backup restored on purpose: refused by default, recorded as a gap when explicitly accepted.
            cli("--data", f"{d}/store", "backup", f"{d}/backup")
            cli("--data", f"{d}/store", "run", "rasid", "examples/rasid_oracle.json")
            cli("--data", f"{d}/store", "anchor", "--witness", f"{d}/witness")
            refused = subprocess.run([sys.executable, "-m", "mizan", "--data", f"{d}/store", "restore", f"{d}/backup", f"{d}/r1"],
                                     cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(json.loads(refused.stderr)["error"], "HISTORY_GAP")
            cli("--data", f"{d}/store", "restore", f"{d}/backup", f"{d}/r2", "--accept-history-gap")
            gaps = cli("--data", f"{d}/r2", "anchor", "--witness", f"{d}/witness")["recovery_gaps"]
            self.assertEqual([(g["tenant"], g["lost_from"], g["lost_to"]) for g in gaps], [("local", 7, 9)])  # submitted, claimed, published after the backup


if __name__ == "__main__":
    unittest.main()
