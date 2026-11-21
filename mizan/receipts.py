"""Offline receipt verification for job outputs (`run --out`) and exhibit packets. Opens no store."""
import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .core.artifacts import DOMAIN
from .core.contract import Refused, canonical, digest, require


def signing_key(public_b64, fingerprint, expected_key=None):
    """Load an embedded public key. Only `expected_key`, obtained out of band, establishes publisher identity."""
    try:
        public = base64.b64decode(public_b64, validate=True)
        key = Ed25519PublicKey.from_public_bytes(public)
    except (ValueError, TypeError) as exc:
        raise Refused("KEY", "Malformed embedded public key") from exc
    found = digest(public)
    require(found == fingerprint and (expected_key is None or expected_key == found), "KEY", "Unexpected signing key")
    return key, found


def check_result_seal(key, fingerprint, *, tenant, job, input_digest, profile_digest, deployment, epoch, generation, result, seal):
    """Recompute the signed execution context and the result digest, then check the Ed25519 signature."""
    context = digest([job, input_digest, profile_digest, deployment, epoch, generation])
    body = {"schema": "1", "tenant": tenant, "kind": "result", "key_id": fingerprint,
            "context": context, "object": digest(result)}
    require(isinstance(seal, dict) and seal.get("body") == body, "RECEIPT", "Result or execution context changed")
    try:
        key.verify(base64.b64decode(seal["signature"], validate=True), DOMAIN + canonical(body))
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise Refused("BAD_SIGNATURE", "Receipt signature did not verify") from exc
    return body


def verify_job_output(packet, expected_key=None):
    """Verify the file written by `python -m mizan run ... --out FILE`."""
    require(isinstance(packet, dict) and packet.get("state") == "SUCCEEDED", "STATE", "Only succeeded jobs carry a receipt")
    prov = packet.get("provenance")
    require(isinstance(prov, dict), "PROVENANCE", "Output predates release 2.2 provenance; rerun the job to verify offline")
    key, fingerprint = signing_key(prov.get("public_key_b64"), prov.get("key_fingerprint"), expected_key)
    result = packet["result"]
    require(isinstance(result, dict) and result.get("product") == str(packet.get("kind", "")).upper(),
            "PROFILE", "Result product does not match the job kind")
    body = check_result_seal(key, fingerprint, tenant=prov["tenant"], job=packet["id"], input_digest=prov["input_digest"],
                             profile_digest=prov["profile_digest"], deployment=packet["rev"], epoch=packet["epoch"],
                             generation=prov["generation"], result=result, seal=packet.get("receipt"))
    return {"verified": "job", "job": packet["id"], "kind": packet["kind"], "engine": result.get("engine"),
            "result_digest": body["object"], "key_fingerprint": fingerprint,
            "publisher_identity": "pinned" if expected_key else "embedded key only (pass --expected-key)"}


def verify_exhibit(packet, expected_key=None):
    """Verify every result in a demo/exhibit packet against its receipt."""
    run = packet["run"]
    key, fingerprint = signing_key(run["public_key_b64"], run["key_fingerprint"], expected_key)
    seen, jobs = set(), set()
    for receipt in run["receipts"]:
        label, job = receipt["fixture"], receipt["job"]
        require(label not in seen and job not in jobs, "DUPLICATE", "Repeated fixture or job receipt")
        seen.add(label)
        jobs.add(job)
        require(label in packet["results"], "COVERAGE", "Receipt refers to a missing result")
        body = check_result_seal(key, fingerprint, tenant=receipt["tenant"], job=job, input_digest=receipt["input_digest"],
                                 profile_digest=receipt["profile_digest"], deployment=receipt["deployment"],
                                 epoch=receipt["epoch"], generation=receipt["generation"],
                                 result=packet["results"][label], seal=receipt["seal"])
        require(body["object"] == receipt["result_digest"], "RECEIPT", "Result digest field changed")
    require(seen == set(packet["results"]) and len(seen) > 0, "COVERAGE", "Exhibit has unsigned or missing results")
    return len(seen)


def verify_any(packet, expected_key=None):
    if isinstance(packet, dict) and "run" in packet and "results" in packet:
        count = verify_exhibit(packet, expected_key)
        return {"verified": "exhibit", "results": count, "key_fingerprint": packet["run"]["key_fingerprint"],
                "publisher_identity": "pinned" if expected_key else "embedded key only (pass --expected-key)"}
    return verify_job_output(packet, expected_key)
