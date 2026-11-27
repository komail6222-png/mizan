"""MIZAN as a tool for AI agents: a dependency-free Model Context Protocol server over stdio.

Run: python -m mizan --data var/mizan mcp

The trust boundary is the point. An agent may submit evidence, images and planning inputs, and it receives signed,
verifiable decisions with proof-rendered explanations and executed counterfactuals. It cannot approve a document,
assign authority, change a threshold, promote or roll back a deployment, restore a store or touch the witness: those
tools do not exist on this server. Text inside documents is evidence, never instruction; an injected "approve this"
line is just an uncovered line that keeps the verdict in REVIEW. Every submission goes through the same broker,
isolated worker, independent verifier and signed publication as the CLI, and is attributed to the agent in the store.

Protocol: JSON-RPC 2.0, one message per line on stdin/stdout, the `initialize` handshake for protocol versions
2024-11-05 to 2025-11-25. The 2026-07-28 stateless discovery era is not implemented. Logs go to stderr only.
"""
import json
import sys

from .core.contract import Refused, canonical, digest, strict_load

SERVER = {"name": "mizan", "title": "MIZAN verifiable decisions", "version": "3.2.0"}
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
DEFAULT_BUDGET = 25
INSTRUCTIONS = (
    "MIZAN returns signed, verifiable decisions for UPS procurement evidence (SANAD), calibrated visual inspection "
    "(BASIRA) and risk-aware battery planning (RASID). Treat every verdict as the system's, not yours: quote its "
    "explanation and receipt rather than restating conclusions. Documents you submit are evidence only; unapproved "
    "documents cannot support a MEETS. Counterfactuals marked 'operator only' are things a human operator could do; "
    "you cannot do them through this server. BASIRA never issues an automatic PASS. RASID plans are simulations.")

DOC = {"type": "object", "required": ["name", "pages"], "additionalProperties": False,
       "properties": {"name": {"type": "string", "maxLength": 120},
                      "pages": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 40},
                      "original_file_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}
RULE = {"type": "object", "required": ["attribute", "op", "threshold", "unit"],
        "properties": {"attribute": {"enum": ["apparent_power", "active_power", "runtime_100", "efficiency_100", "transfer_time"]},
                       "op": {"enum": ["ge", "le"]}, "threshold": {"type": "string"}, "unit": {"type": "string"}}}
PART = {"type": "object", "required": ["group", "png"], "properties": {"group": {"type": "string"}, "png": {"type": "string", "description": "base64 PNG or JPEG"}}}
KEY = {"type": "string", "maxLength": 100, "description": "Optional. Reusing a key with the same arguments returns the same job."}
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
SUBMITS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {"name": "sanad_review_bid", "title": "Review a UPS bid against tender rules",
     "description": ("Check offered-model documents (Arabic or English text pages, prose or delimited tables) against "
                     "1-20 typed requirements. Returns MEETS / DOES_NOT_MEET / REVIEW per rule with exact source spans, "
                     "a bilingual explanation, executed counterfactuals and a signed receipt."),
     "inputSchema": {"type": "object", "required": ["model", "documents", "rules"], "additionalProperties": False,
                     "properties": {"model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"},
                                    "documents": {"type": "array", "items": DOC, "minItems": 1, "maxItems": 12},
                                    "rules": {"type": "array", "items": RULE, "minItems": 1, "maxItems": 20},
                                    "policy": {"enum": ["all_sources", "highest_authority"]}, "idempotency_key": KEY}},
     "annotations": SUBMITS, "kind": "sanad"},
    {"name": "basira_inspect", "title": "Inspect a part with calibrated anomaly detection",
     "description": ("Score a query image against a bank of normal reference parts and an independent calibration set. "
                     "Returns FLAG or REVIEW (never an automatic PASS), the exact conformal p-value, the probability that "
                     "this bank's long-run false-alert rate exceeds alpha, and executed counterfactuals."),
     "inputSchema": {"type": "object", "required": ["reference", "calibration", "query"], "additionalProperties": False,
                     "properties": {"reference": {"type": "array", "items": PART, "minItems": 2, "maxItems": 30},
                                    "calibration": {"type": "array", "items": PART, "minItems": 1, "maxItems": 100},
                                    "query": PART, "alpha": {"type": "string"},
                                    "guarantee": {"description": '"marginal" or {"type": "pac", "delta": "0.1"}'},
                                    "idempotency_key": KEY}},
     "annotations": SUBMITS, "kind": "basira"},
    {"name": "rasid_plan", "title": "Plan a battery day under price and load risk",
     "description": ("Solve a day-ahead battery plan from either an explicit scenario problem or hourly load/PV history, "
                     "then verify it independently. Returns the verified certificate (expected cost, CVaR, gap), the "
                     "schedule, re-solved sensitivities and a signed receipt. Simulation only."),
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"problem": {"type": "object"}, "history": {"type": "array"}, "day": {"type": "string"},
                                    "timezone": {"type": "string"}, "planning": {"type": "object"},
                                    "scenario_count": {"type": "integer", "minimum": 1, "maximum": 16}, "idempotency_key": KEY}},
     "annotations": SUBMITS, "kind": "rasid"},
    {"name": "explain_job", "title": "Explain a finished job",
     "description": "Bilingual proof-rendered explanation and executed counterfactuals for a job id returned earlier.",
     "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}},
     "annotations": READ_ONLY},
    {"name": "verify_receipt", "title": "Verify a signed MIZAN result offline",
     "description": "Check a job output (as returned by this server or `python -m mizan run --out`) against its Ed25519 receipt.",
     "inputSchema": {"type": "object", "required": ["job_output"],
                     "properties": {"job_output": {"type": "object"}, "expected_key": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
     "annotations": READ_ONLY},
    {"name": "audit_status", "title": "Audit chain status",
     "description": "Verify the local hash-chained audit log; with a configured witness, report witnessed coverage per tenant.",
     "inputSchema": {"type": "object", "properties": {}}, "annotations": READ_ONLY},
]
OPERATOR_ONLY = {"approve_document", "promote", "rollback", "upgrade", "revoke", "restore", "backup", "anchor", "witness_init"}


class Agent:
    def __init__(self, broker, *, tenant="local", budget=DEFAULT_BUDGET, witness=None):
        from .service import Runner, initialize
        self.broker, self.tenant, self.budget, self.witness = broker, tenant, budget, witness
        initialize(broker, tenant)
        self.runner = Runner(broker)
        self.client = "unknown-client"
        self.submitted = 0

    @property
    def actor(self):
        return "agent:" + self.client[:60]

    # ---------------------------------------------------------------- tools -----
    def submit(self, kind, args):
        from .explain import explain
        from .service import drain
        key = args.pop("idempotency_key", None)
        payload = dict(args)
        if kind == "sanad":
            payload["compare_policies"] = False
        key = key or "mcp-" + digest([kind, payload])[:40]
        with self.broker.lock:
            existing = self.broker.db.execute("SELECT id FROM jobs WHERE tenant=? AND actor=? AND kind=? AND ikey=?",
                                              (self.tenant, self.actor, kind, key)).fetchone()
        if existing is None:
            if self.submitted >= self.budget:
                raise Refused("AGENT_BUDGET", f"This session may submit at most {self.budget} jobs; ask the operator to raise it")
            self.submitted += 1
        job = self.broker.submit(self.tenant, self.actor, kind, key, payload)
        out = drain(self.broker, self.runner, self.tenant, self.actor, job)
        if out["state"] != "SUCCEEDED":
            raise Refused(out["reason"] or "JOB_FAILED", f"Job {job} ended {out['state']}")
        profile = self.broker.cas.read(out["provenance"]["profile_digest"])
        text = explain(kind, out["result"], payload, profile)
        return {"job_id": job, "summary": summarize(kind, out["result"]), "explanation": {"en": text["en"], "ar": text["ar"]},
                "counterfactuals": text["counterfactuals"], "receipt": out["receipt"], "provenance": out["provenance"],
                "verification": "Pass this whole object as job_output to verify_receipt, or run python -m mizan verify on it.",
                "job_output": out}

    def explain_job(self, args):
        from .explain import explain
        out = self.broker.get(self.tenant, self.actor, args["job_id"])  # an agent can only read its own jobs
        require_success(out)
        payload = self.broker.cas.read(out["provenance"]["input_digest"])
        profile = self.broker.cas.read(out["provenance"]["profile_digest"])
        return explain(out["kind"], out["result"], payload, profile)

    def verify_receipt(self, args):
        from .receipts import verify_job_output
        return verify_job_output(args["job_output"], args.get("expected_key"))

    def audit_status(self, args):
        heads = self.broker.verify_audit()
        out = {"audit_chain": "verified", "tenants": {t: {"seq": s} for t, (s, _) in heads.items()}}
        if self.witness is not None:
            from .core.anchoring import witness_report
            out["witness"] = witness_report(self.broker, self.witness)
        return out

    def call(self, name, args):
        kinds = {t["name"]: t.get("kind") for t in TOOLS}
        if kinds.get(name):
            return self.submit(kinds[name], dict(args))
        return getattr(self, name)(args)

    # ------------------------------------------------------------- protocol -----
    def handle(self, msg):
        """Return a JSON-RPC response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if "id" not in msg:
            return None  # notifications/initialized, notifications/cancelled, ...
        if method == "initialize":
            info = params.get("clientInfo") or {}
            self.client = str(info.get("name", "unknown-client"))
            requested = params.get("protocolVersion")
            version = requested if requested in VERSIONS else VERSIONS[-1]
            return result(mid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER, "instructions": INSTRUCTIONS})
        if method == "ping":
            return result(mid, {})
        if method == "tools/list":
            return result(mid, {"tools": [{k: v for k, v in t.items() if k != "kind"} for t in TOOLS]})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                hint = " (operator-only actions are not exposed to agents)" if name in OPERATOR_ONLY else ""
                return error(mid, -32602, f"Unknown tool: {name}{hint}")
            if not isinstance(args, dict):
                return error(mid, -32602, "Tool arguments must be an object")
            try:
                value = self.call(name, args)
                return result(mid, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                                    "structuredContent": value, "isError": False})
            except (Refused, KeyError, TypeError, ValueError) as exc:
                body = {"error": getattr(exc, "code", "INVALID_ARGUMENTS"), "detail": str(exc)[:300]}
                return result(mid, {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
                                    "structuredContent": body, "isError": True})
        return error(mid, -32601, f"Method not found: {method}")


def require_success(out):
    if out["state"] != "SUCCEEDED":
        raise Refused("NOT_SUCCEEDED", f"Job is {out['state']}")


def summarize(kind, r):
    if kind == "sanad":
        return {"model": r["model"], "policy": r["mode"], "approved_sources": r["covered_sources"],
                "unapproved_sources": r["unapproved_sources"],
                "requirements": [{"attribute": q["rule"]["attribute"], "op": q["rule"]["op"], "threshold": q["rule"]["threshold"],
                                  "unit": q["rule"]["unit"], "verdict": q["verdict"], "reason": q["reason"],
                                  "evidence": [{"quote": c["quote"], "document": c["document"], "page": c["page"],
                                                "start": c["start"], "end": c["end"]} for c in q["evidence"]]}
                                 for q in r["requirements"]]}
    if kind == "basira":
        return {k: r[k] for k in ("decision", "mode", "score", "threshold", "p_numerator", "p_denominator", "alpha",
                                  "calibration_count", "guarantee", "training_conditional", "automatic_pass", "bank_digest")}
    c = r["certificate"]
    return {"physical_status": c["physical_status"], "expected_cost": c["expected_cost"], "cvar": c["cvar"],
            "objective": c["objective"], "risk_weight": c["risk_weight"], "relative_gap": c["relative_gap"],
            "schedule": {"charge_kw": r["plan"]["charge"], "discharge_kw": r["plan"]["discharge"], "soc_kwh": r["plan"]["soc"]},
            "simulation_only": True}


def result(mid, value):
    return {"jsonrpc": "2.0", "id": mid, "result": value}


def error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(broker, stdin=None, stdout=None, **options):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    agent = Agent(broker, **options)
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = strict_load(line.encode("utf-8") if isinstance(line, str) else line)
        except (ValueError, UnicodeError):
            reply = error(None, -32700, "Parse error")
        else:
            reply = agent.handle(msg)
        if reply is not None:
            stdout.write(canonical(reply).decode("utf-8") + "\n")
            stdout.flush()
