"""Audit witness: a separate trust domain that countersigns the broker's hash-chained audit log.

Architecture §5.4 asks for an independently administered witness that "retains the last accepted generation/sequence/
hash, rejects forks or regressions, and signs receipts". This module implements that protocol with its own Ed25519
key and its own SQLite state. It mirrors only entry hashes, never event bodies after verification, which is enough to
check that every new checkpoint extends what it has already witnessed.

Running the witness in the same process or on the same host is a *protocol simulation*. Independence from the
broker's administrator is a deployment property that this code cannot demonstrate: give the witness its own host,
account and backups before relying on it.
"""
import base64
import contextlib
import fcntl
import json
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .artifacts import install
from .contract import Refused, canonical, digest, require
from .store import connect

DOMAIN = b"MIZAN/audit-witness/v1\0"
GENESIS = "0" * 64
MAX_ENTRIES = 50000
SCHEMA = """
CREATE TABLE IF NOT EXISTS generations(store TEXT, generation TEXT, parent TEXT, superseded_by TEXT,
    registered_ns INTEGER NOT NULL, gaps TEXT NOT NULL, PRIMARY KEY(store,generation));
CREATE TABLE IF NOT EXISTS chain(store TEXT, generation TEXT, tenant TEXT, seq INTEGER, hash TEXT NOT NULL,
    PRIMARY KEY(store,generation,tenant,seq));
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, generation TEXT, tenant TEXT,
    action TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT, time_ns INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS chain_no_update BEFORE UPDATE ON chain BEGIN SELECT RAISE(ABORT,'immutable chain'); END;
CREATE TRIGGER IF NOT EXISTS chain_no_delete BEFORE DELETE ON chain BEGIN SELECT RAISE(ABORT,'immutable chain'); END;
"""


def entry_hash(tenant, entry):
    """The broker's chain rule (mizan.core.store.Broker.log), recomputed independently."""
    return digest(["mizan.audit.v1", tenant, entry["seq"], entry["prev"], entry["body"]])


def verify_receipt(receipt, public_b64, expected=None):
    """Check a witness receipt offline. `expected` fields (store, generation, tenant, seq, hash, ...) must match."""
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True))
        body = receipt["body"]
        key.verify(base64.b64decode(receipt["signature"], validate=True), DOMAIN + canonical(body))
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise Refused("WITNESS_SIGNATURE", "Witness receipt did not verify") from exc
    require(body.get("witness_key") == digest(base64.b64decode(public_b64)), "WITNESS_KEY", "Receipt from another witness")
    for name, value in (expected or {}).items():
        require(body.get(name) == value, "WITNESS_SCOPE", f"Receipt {name} does not match")
    return body


class Witness:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.host = open(self.root / "witness.lock", "a+b")
        try:
            fcntl.flock(self.host, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.host.close()
            raise Refused("WITNESS_IN_USE", "Another process owns this witness") from exc
        keypath = self.root / "keys" / "witness.key"
        if not keypath.exists():
            require(not (self.root / "witness.db").exists(), "WITNESS_KEY_MISSING", "Existing witness lost its key")
            install(keypath, Ed25519PrivateKey.generate().private_bytes_raw())
        self.key = Ed25519PrivateKey.from_private_bytes(keypath.read_bytes())
        raw = self.key.public_key().public_bytes_raw()
        self.public_b64 = base64.b64encode(raw).decode()
        self.key_id = digest(raw)
        install(self.root / "keys" / "witness.pub", raw)
        self.db = connect(self.root / "witness.db")
        require(self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal", "WAL", "WAL mode unavailable")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()
        self.host.close()

    @contextlib.contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def sign(self, body):
        body = {**body, "schema": "1", "witness_key": self.key_id, "witnessed_ns": time.time_ns()}
        return {"body": body, "signature": base64.b64encode(self.key.sign(DOMAIN + canonical(body))).decode()}

    def record(self, store, generation, tenant, action, outcome, detail=None):
        # Rejections are recorded in their own transaction so a refused request still leaves evidence.
        with self.tx() as db:
            db.execute("INSERT INTO decisions(store,generation,tenant,action,outcome,detail,time_ns) VALUES(?,?,?,?,?,?,?)",
                       (store, generation, tenant, action, outcome, detail, time.time_ns()))

    # ---- read side -------------------------------------------------------------------------------------------
    def generation(self, store, generation):
        row = self.db.execute("SELECT * FROM generations WHERE store=? AND generation=?", (store, generation)).fetchone()
        return None if row is None else {k: row[k] for k in row.keys()}

    def head(self, store, generation, tenant):
        """Latest witnessed (seq, hash) for a tenant in a generation, following the lineage below its base."""
        row = self.db.execute("SELECT seq,hash FROM chain WHERE store=? AND generation=? AND tenant=? ORDER BY seq DESC LIMIT 1",
                              (store, generation, tenant)).fetchone()
        if row is not None:
            return row["seq"], row["hash"]
        g = self.generation(store, generation)
        return self.head(store, g["parent"], tenant) if g and g["parent"] else (0, GENESIS)

    def hash_at(self, store, generation, tenant, seq):
        if seq == 0:
            return GENESIS
        row = self.db.execute("SELECT hash FROM chain WHERE store=? AND generation=? AND tenant=? AND seq=?",
                              (store, generation, tenant, seq)).fetchone()
        if row is not None:
            return row["hash"]
        g = self.generation(store, generation)
        return self.hash_at(store, g["parent"], tenant, seq) if g and g["parent"] else None

    def tenants(self, store, generation):
        names = set()
        while generation:
            names.update(r[0] for r in self.db.execute("SELECT DISTINCT tenant FROM chain WHERE store=? AND generation=?",
                                                       (store, generation)))
            g = self.generation(store, generation)
            generation = g["parent"] if g else None
        return names

    # ---- write side ------------------------------------------------------------------------------------------
    def _extend(self, db, store, generation, tenant, entries, start, allowed_generations):
        """Verify that entries continue the chain from `start` and append their hashes."""
        seq, prev = start
        require(isinstance(entries, list) and len(entries) <= MAX_ENTRIES, "WITNESS_BATCH", "Bounded entry batch required")
        for e in entries:
            require(isinstance(e, dict) and set(e) == {"seq", "prev", "body", "hash"} and isinstance(e["body"], dict),
                    "WITNESS_ENTRY", "Malformed audit entry")
            require(e["seq"] == seq + 1, "WITNESS_GAP", f"Expected sequence {seq + 1} for {tenant}, got {e['seq']}")
            require(e["prev"] == prev, "WITNESS_FORK", f"Entry {e['seq']} for {tenant} does not extend the witnessed chain")
            require(entry_hash(tenant, e) == e["hash"], "WITNESS_HASH", f"Entry {e['seq']} for {tenant} has a wrong hash")
            require(e["body"].get("generation") in allowed_generations, "WITNESS_GENERATION",
                    f"Entry {e['seq']} for {tenant} was written by another generation")
            db.execute("INSERT INTO chain VALUES(?,?,?,?,?)", (store, generation, tenant, e["seq"], e["hash"]))
            seq, prev = e["seq"], e["hash"]
        return seq, prev

    def checkpoint(self, store, generation, tenant, entries):
        """Accept the next audit entries of one tenant and return a signed checkpoint receipt.

        A first checkpoint for an unknown store registers it as a root generation and must start at sequence 1.
        A displaced (superseded) generation can no longer extend its history."""
        try:
            with self.tx() as db:
                g = self.generation(store, generation)
                if g is None:
                    known = db.execute("SELECT 1 FROM generations WHERE store=? LIMIT 1", (store,)).fetchone()
                    require(not known, "WITNESS_UNREGISTERED", "Register this restored generation before checkpointing")
                    db.execute("INSERT INTO generations VALUES(?,?,?,?,?,?)", (store, generation, None, None, time.time_ns(), "[]"))
                    g = self.generation(store, generation)
                require(g["superseded_by"] is None, "WITNESS_SUPERSEDED",
                        f"Generation {generation} was replaced by {g['superseded_by']}")
                start = self.head(store, generation, tenant)
                seq, value = self._extend(db, store, generation, tenant, entries, start, {generation})
                receipt = self.sign({"kind": "checkpoint", "store": store, "generation": generation, "tenant": tenant,
                                     "seq": seq, "hash": value, "previous_seq": start[0]})
            self.record(store, generation, tenant, "checkpoint", "accepted", f"{start[0]}->{seq}")
            return receipt
        except Refused as exc:
            self.record(store, generation, tenant, "checkpoint", exc.code, str(exc))
            raise

    def register_generation(self, store, generation, parent, heads, proofs=None):
        """Register a restored generation. `heads` are the restored store's per-tenant (seq, hash) at restore time.

        For each tenant the restored head must lie on the witnessed lineage of `parent`:
          * behind the witnessed head  -> accepted with an explicit recovery gap (events seq+1..head were lost);
          * equal                      -> continuous;
          * ahead                      -> `proofs[tenant]` must supply the unwitnessed entries that connect them.
        A head that disagrees with a witnessed hash is a fork and is refused. A parent can be superseded only once."""
        proofs = proofs or {}
        try:
            with self.tx() as db:
                require(self.generation(store, generation) is None, "WITNESS_DUPLICATE", "Generation already registered")
                p = self.generation(store, parent)
                require(p is not None, "WITNESS_UNKNOWN_PARENT", "The witness never saw the parent generation")
                require(p["superseded_by"] is None, "WITNESS_SUPERSEDED",
                        f"Parent {parent} was already replaced by {p['superseded_by']}; reconcile before a second restore")
                db.execute("INSERT INTO generations VALUES(?,?,?,?,?,?)", (store, generation, parent, None, time.time_ns(), "[]"))
                gaps = []
                for tenant in sorted(self.tenants(store, parent) | set(heads)):
                    seq, value = heads.get(tenant, (0, GENESIS))
                    wseq, whash = self.head(store, parent, tenant)
                    if seq <= wseq:
                        require(self.hash_at(store, parent, tenant, seq) == value, "WITNESS_FORK",
                                f"Restored head {seq} for {tenant} disagrees with witnessed history")
                        if seq < wseq:
                            gaps.append({"tenant": tenant, "lost_from": seq + 1, "lost_to": wseq})
                        # Base of the new lineage, including seq 0, so the parent's longer head is not inherited.
                        db.execute("INSERT INTO chain VALUES(?,?,?,?,?)", (store, generation, tenant, seq, value))
                    else:
                        require(proofs.get(tenant), "WITNESS_PROOF_REQUIRED",
                                f"Restored head {seq} for {tenant} is ahead of witnessed {wseq}; supply the connecting entries")
                        end = self._extend(db, store, generation, tenant, proofs[tenant], (wseq, whash), {parent})
                        require(end == (seq, value), "WITNESS_FORK", f"Proof for {tenant} does not reach the restored head")
                db.execute("UPDATE generations SET gaps=? WHERE store=? AND generation=?", (json.dumps(gaps), store, generation))
                db.execute("UPDATE generations SET superseded_by=? WHERE store=? AND generation=?", (generation, store, parent))
                receipt = self.sign({"kind": "generation", "store": store, "generation": generation, "parent": parent,
                                     "recovery_gaps": gaps})
            self.record(store, generation, None, "register_generation", "accepted", json.dumps(gaps))
            return receipt
        except Refused as exc:
            self.record(store, generation, None, "register_generation", exc.code, str(exc))
            raise
