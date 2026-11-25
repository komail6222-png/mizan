"""JSON job API and read-only public exhibit. Private writes require the local operator token."""
import contextlib
import hmac
import json
import os
import secrets
import shutil
import time
import uuid
from pathlib import Path
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from .core.contract import Refused,strict_load,require
from .core.store import Broker
from .core.artifacts import install
from .service import Runner,PRODUCTS,initialize

STATIC=Path(__file__).parent/"web"

def app_factory(data=None,public=None):
    public=(os.getenv("MIZAN_PUBLIC_DEMO","0")=="1") if public is None else public
    root=Path(data or os.environ.get("MIZAN_DATA","var/mizan"))
    totals={"http_requests":0,"http_errors":0}
    @contextlib.asynccontextmanager
    async def lifespan(app):
        if not public:
            broker=Broker(root);app.state.broker=broker
            try:
                initialize(broker,"local")
                if not (root/"operator.token").exists():install(root/"operator.token",secrets.token_urlsafe(32).encode())
                app.state.token=(root/"operator.token").read_text().strip()
                runner=Runner(broker);app.state.runner=runner;runner.start()
                try:yield
                finally:runner.close()
            finally:broker.close()
        else:yield
    api=FastAPI(title="MIZAN",version="3.1.0",lifespan=lifespan)
    @api.middleware("http")
    async def trace(request,call_next):
        tid=uuid.uuid4().hex;start=time.perf_counter()
        reply=await call_next(request);totals["http_requests"]+=1
        if reply.status_code>=400:totals["http_errors"]+=1
        reply.headers["X-Request-ID"]=tid;reply.headers["X-Content-Type-Options"]="nosniff"
        reply.headers["Referrer-Policy"]="no-referrer"
        print(json.dumps({"event":"http","trace":tid,"method":request.method,"status":reply.status_code,
                          "elapsed_ms":round((time.perf_counter()-start)*1000,2)}),flush=True)
        return reply
    def auth(req):
        require(not public,"READ_ONLY","Public exhibit only serves the bundled demonstrations")
        got=req.headers.get("Authorization","")
        require(hmac.compare_digest(got.encode(),("Bearer "+api.state.token).encode()),"UNAUTHORIZED","A local operator token is required")
    @api.exception_handler(Refused)
    async def refused(request,exc):
        status={"UNAUTHORIZED":401,"READ_ONLY":403,"NOT_FOUND":404,"KIND":404,"IDEMPOTENCY_CONFLICT":409,"QUEUE_FULL":429,"INPUT_LIMIT":413,
                "DISK_RESERVE":503,"NOT_READY":503}.get(exc.code,422)
        return JSONResponse({"error":exc.code,"detail":str(exc)},status_code=status)
    @api.get("/",response_class=HTMLResponse)
    def index():
        page=STATIC/"index.html"
        if not page.exists():return HTMLResponse("Generate the demo with python -m mizan demo.",status_code=503)
        return HTMLResponse(page.read_text(),headers={"Content-Security-Policy":"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'"})
    @api.get("/healthz")
    def health():return {"status":"ok","profile":"public-exhibit" if public else "local-cpu"}
    @api.get("/readyz")
    def ready():
        if public:return {"ready":(STATIC/"index.html").exists(),"writes":False}
        b=api.state.broker;runner=api.state.runner.health()
        with b.lock:rows=b.db.execute("SELECT subject,state FROM deployments WHERE tenant='local'").fetchall()
        return {"ready":all(r["state"]=="READY" for r in rows) and len(rows)==3 and runner["healthy"],
                "subjects":{r["subject"]:r["state"] for r in rows},"runner":runner}
    @api.get("/capabilities")
    def capabilities():return {"domains":PRODUCTS,"public_read_only":public,"gpu_qualified":False,"external_witness":False,"compute":"CPU"}
    @api.post("/api/v1/jobs/{kind}",status_code=202)
    async def submit(kind:str,request:Request):
        auth(request);require(kind in PRODUCTS,"KIND","Unknown product")
        data=bytearray()
        async for piece in request.stream():
            require(len(data)+len(piece)<=4<<20,"INPUT_LIMIT","Input exceeds 4 MiB");data.extend(piece)
        try:payload=strict_load(data)
        except (ValueError,UnicodeError) as exc:raise Refused("INVALID_JSON","Expected a JSON object") from exc
        require(isinstance(payload,dict),"INPUT","Expected a JSON object")
        j=api.state.broker.submit("local","operator",kind,request.headers.get("idempotency-key",""),payload)
        api.state.runner.wake.set()
        return {"job_id":j,"status_url":"/api/v1/jobs/"+j}
    @api.get("/api/v1/jobs/{job_id}")
    def get(job_id:str,request:Request):
        auth(request);return api.state.broker.get("local","operator",job_id)
    @api.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id:str,request:Request):
        auth(request);return {"state":api.state.broker.cancel("local","operator",job_id)}
    @api.get("/api/v1/metrics")
    def metrics(request:Request):
        auth(request);b=api.state.broker
        with b.lock:states=dict(b.db.execute("SELECT state,count(*) FROM jobs GROUP BY state").fetchall())
        return {"http":dict(totals),"jobs":states,"wal":b.checkpoint(),"runner":api.state.runner.health()}
    return api
