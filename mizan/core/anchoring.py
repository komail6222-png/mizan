"""Broker side of the witness protocol: anchor the local audit chain and report what the witness can vouch for."""
import json
import time
from pathlib import Path

from .contract import Refused, canonical, require
from .witness import GENESIS, verify_receipt


def separate_domains(broker, witness):
    b, w = Path(broker.root).resolve(), Path(witness.root).resolve()
    require(b != w and b not in w.parents and w not in b.parents, "WITNESS_DOMAIN",
            "The witness directory must not share the broker's data directory")


def restore_parent(broker):
    """Parent generation recorded by `restore` for the broker's current generation, if any."""
    for e in reversed(broker.audit_entries("system")):
        if e["body"]["action"] == "restore" and e["body"]["generation"] == broker.generation:
            return e["body"]["fields"]["parent"]
    return None


def keep(broker, tenant, receipt):
    body = receipt["body"]
    with broker.tx() as db:
        db.execute("INSERT OR REPLACE INTO witness_receipts VALUES(?,?,?,?)",
                   (tenant, body["generation"], body.get("seq", 0), canonical(receipt).decode()))


def register_restored(broker, witness):
    parent = restore_parent(broker)
    require(parent is not None, "WITNESS_UNREGISTERED", "Witness knows this store but not this generation, and no restore event names a parent")
    declared, proofs = {}, {}
    for tenant in broker.verify_audit():
        # Restore changes the generation, so the pre-restore history is exactly the entries of older generations.
        before = [e for e in broker.audit_entries(tenant) if e["body"]["generation"] != broker.generation]
        declared[tenant] = (before[-1]["seq"], before[-1]["hash"]) if before else (0, GENESIS)
        wseq, _ = witness.head(broker.store_id, parent, tenant)
        if declared[tenant][0] > wseq:
            proofs[tenant] = [e for e in before if e["seq"] > wseq]
    receipt = witness.register_generation(broker.store_id, broker.generation, parent, declared, proofs)
    keep(broker, "#generation", receipt)
    return receipt


def anchor(broker, witness):
    """Send every unwitnessed audit entry to the witness and store its receipts."""
    separate_domains(broker, witness)
    out = {"store": broker.store_id, "generation": broker.generation, "witness_key": witness.key_id, "tenants": {}}
    known = witness.generation(broker.store_id, broker.generation)
    store_seen = witness.db.execute("SELECT 1 FROM generations WHERE store=? LIMIT 1", (broker.store_id,)).fetchone()
    if known is None and store_seen:
        out["recovery_gaps"] = register_restored(broker, witness)["body"]["recovery_gaps"]
    for tenant, (seq, value) in sorted(broker.verify_audit().items()):
        wseq, whash = witness.head(broker.store_id, broker.generation, tenant)
        require(wseq <= seq, "HISTORY_REGRESSED", f"Local {tenant} history ends at {seq}; the witness saw {wseq}")
        if wseq == seq:
            require(whash == value, "HISTORY_DIVERGED", f"Local {tenant} head differs from the witnessed hash")
            out["tenants"][tenant] = {"seq": seq, "sent": 0}
            continue
        receipt = witness.checkpoint(broker.store_id, broker.generation, tenant, broker.audit_entries(tenant, wseq))
        keep(broker, tenant, receipt)
        out["tenants"][tenant] = {"seq": receipt["body"]["seq"], "sent": seq - wseq}
    return out


def witness_report(broker, witness=None, public_b64=None):
    """Per-tenant assurance: witnessed head, unanchored tail and consistency.

    With a live witness the report compares against the witness's own state, which also detects deleted receipts and
    a replaced local database. With only the witness public key it can check the stored receipts, which is weaker."""
    require(witness is not None or public_b64, "WITNESS", "Pass a witness or its public key")
    if witness is not None:
        separate_domains(broker, witness)
    public = witness.public_b64 if witness is not None else public_b64
    heads = broker.verify_audit()
    local = {t: {e["seq"]: e for e in broker.audit_entries(t)} for t in heads}
    tenants, now = {}, time.time_ns()
    names = set(heads)
    if witness is not None and witness.generation(broker.store_id, broker.generation):
        names |= witness.tenants(broker.store_id, broker.generation)
    for tenant in sorted(names):
        seq = heads.get(tenant, (0, GENESIS))[0]
        if witness is not None:
            if witness.generation(broker.store_id, broker.generation) is None:
                wseq, whash = 0, GENESIS
            else:
                wseq, whash = witness.head(broker.store_id, broker.generation, tenant)
            source = "live witness"
        else:
            rows = broker.db.execute("SELECT receipt FROM witness_receipts WHERE tenant=? AND generation=? ORDER BY seq DESC LIMIT 1",
                                     (tenant, broker.generation)).fetchall()
            if rows:
                body = verify_receipt(json.loads(rows[0][0]), public,
                                      {"store": broker.store_id, "generation": broker.generation, "tenant": tenant})
                wseq, whash = body["seq"], body["hash"]
            else:
                wseq, whash = 0, GENESIS
            source = "stored receipts"
        if wseq > seq:
            status = "REGRESSED"
        elif wseq and local[tenant].get(wseq, {}).get("hash") != whash:
            status = "DIVERGED"
        elif wseq == seq:
            status = "WITNESSED"
        else:
            status = "UNANCHORED_TAIL"
        row = {"local_seq": seq, "witnessed_seq": wseq, "unanchored_entries": max(0, seq - wseq), "status": status}
        if status == "UNANCHORED_TAIL":
            row["oldest_unanchored_age_s"] = round((now - local[tenant][wseq + 1]["body"]["time_ns"]) / 1e9, 3)
        tenants[tenant] = row
    gaps = None
    if witness is not None and witness.generation(broker.store_id, broker.generation):
        gaps = json.loads(witness.generation(broker.store_id, broker.generation)["gaps"])
    consistent = all(r["status"] in ("WITNESSED", "UNANCHORED_TAIL") for r in tenants.values())
    return {"store": broker.store_id, "generation": broker.generation, "source": source, "consistent": consistent,
            "recovery_gaps": gaps, "tenants": tenants,
            "assurance": ("Tamper evidence up to the witnessed sequence for each tenant. The unanchored tail and a "
                          "compromised witness remain outside it; a witness on the same host is a protocol simulation.")}
