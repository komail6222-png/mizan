"""Local execution boundary. Worker proposals are checked before publication."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from .core.contract import canonical,strict_load,digest,require,Refused
from .examples import profile as release_profile

PRODUCTS=("sanad","basira","rasid")


def initialize(broker,tenant):
    for k in PRODUCTS:
        with broker.lock:
            d=broker.db.execute("SELECT rev FROM deployments WHERE tenant=? AND subject=?",(tenant,k)).fetchone()
        if d is None:broker.promote(tenant,k,release_profile(k),0,actor="local-bootstrap")


def upgrade(broker,tenant):
    """Explicitly move deployments to this release's engines. Operator approvals and other fields are preserved;
    each change is a new signed revision with its own audit event. Suspended deployments are left untouched."""
    report={}
    for k in PRODUCTS:
        with broker.lock:
            d=broker.db.execute("SELECT * FROM deployments WHERE tenant=? AND subject=?",(tenant,k)).fetchone()
        if d is None:
            report[k]={"action":"bootstrap","revision":broker.promote(tenant,k,release_profile(k),0,actor="local-bootstrap")}
            continue
        current=broker.cas.open(json.loads(d["seal"]),tenant,"profile",k);target=release_profile(k)["engine"]
        if d["state"]!="READY":
            report[k]={"action":"skipped","state":d["state"],"engine":current.get("engine")}
        elif current.get("engine")==target:
            report[k]={"action":"current","revision":d["rev"],"engine":target}
        else:
            rev=broker.promote(tenant,k,{**current,"engine":target},d["rev"],actor="local-upgrade")
            report[k]={"action":"upgraded","from":current.get("engine"),"to":target,"revision":rev}
    return report


def verify_output(kind,payload,profile,out):
    if kind=="sanad":
        from .sanad.engine import evaluate
        expected=evaluate(payload,profile)
        require(digest(out)==digest(expected),"UNSUPPORTED_PROOF","Extraction proof did not reproduce")
    elif kind=="basira":
        from .basira.engine import evaluate
        # CPU score source is small enough for a complete trusted replay.
        expected=evaluate(payload,profile)
        require(digest(expected)==digest(out),"VISION_REPLAY","Inspection scores or output images did not reproduce")
    elif kind=="rasid":
        from .rasid.engine import materialize
        from .rasid.verify import verify
        p,f=materialize(payload)
        require(digest(p)==digest(out["problem"]) and digest(f)==digest(out["forecast"]),
                "FORGED_INPUT","Solver used a different problem or forecast vintage")
        c=verify(p,out["plan"])
        require(digest(c)==digest(out["certificate"]),"PLAN_REJECTED","Certificate mismatch")
    else:raise Refused("KIND","Unknown product")
    require(out.get("product")==kind.upper() and out.get("engine")==profile["engine"],"PROFILE","Result profile mismatch")
    return out


def drain(broker,runner,tenant,actor,jid,deadline=300.0):
    """Foreground execution for the CLI: run queued work until this job is terminal, without busy-spinning."""
    until=time.monotonic()+deadline
    while broker.get(tenant,actor,jid)["state"] in ("QUEUED","RUNNING"):
        require(time.monotonic()<until,"DRAIN_TIMEOUT","Job did not finish within the foreground deadline")
        if not runner.one():time.sleep(.05)
    return broker.get(tenant,actor,jid)


def event(name,**fields):
    print(json.dumps({"event":name,**fields},sort_keys=True),file=sys.stderr,flush=True)


class Runner:
    """Background dispatcher. A failing job becomes a terminal job error; it never stops the loop."""
    def __init__(self,broker):
        self.broker=broker
        self.physical=threading.BoundedSemaphore(broker.max_running)
        self.stop=threading.Event();self.wake=threading.Event()
        self.thread=None
        self.consecutive_errors=0;self.last_error=None

    def start(self):
        self.thread=threading.Thread(target=self.loop,name="mizan-local-runner",daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(55)

    def health(self):
        alive=self.thread is not None and self.thread.is_alive()
        return {"alive":alive,"consecutive_errors":self.consecutive_errors,"last_error":self.last_error,
                "healthy":alive and self.consecutive_errors<5}

    def loop(self):
        while not self.stop.is_set():
            try:
                worked=self.one();self.consecutive_errors=0
            except Exception as exc:  # Broker or store fault outside a single job: back off, keep serving.
                self.consecutive_errors+=1;self.last_error=type(exc).__name__
                event("runner_error",error=self.last_error,code=getattr(exc,"code",None),consecutive=self.consecutive_errors)
                self.wake.wait(min(5.0,.35*2**min(self.consecutive_errors,4)));self.wake.clear()
                continue
            if not worked:
                self.wake.wait(.35);self.wake.clear()

    def one(self):
        if not self.physical.acquire(blocking=False):return False
        try:
            token=self.broker.claim()
            if token is None:return False
            try:
                value=self.execute(token)
                self.broker.publish(token,value)
            except Exception as exc:
                code=exc.code if isinstance(exc,Refused) else "WORKER_ERROR"
                if code=="SHUTDOWN":
                    # Leave the attempt RUNNING; restart fencing re-queues it within the retry budget.
                    return True
                if not isinstance(exc,Refused):
                    event("job_error",job=token["id"],kind=token["kind"],error=type(exc).__name__)
                try:self.broker.fail(token,code)
                except Refused as stale:
                    if stale.code!="STALE_ATTEMPT":raise
            return True
        finally:self.physical.release()

    def execute(self,t):
        b=self.broker
        payload=b.cas.read(t["payload"]);prof=b.cas.read(t["profile"])
        envelope=canonical(dict(kind=t["kind"],payload=payload,profile=prof))
        package=str(Path(__file__).resolve().parent.parent)
        env={"PATH":os.defpath,"PYTHONPATH":package,"PYTHONNOUSERSITE":"1","PYTHONHASHSEED":"0",
             "OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1",
             "HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","LANG":"C.UTF-8"}
        with tempfile.TemporaryDirectory(prefix="mizan-attempt-") as tmp:
            with tempfile.TemporaryFile() as stdin,tempfile.TemporaryFile() as out:
                stdin.write(envelope);stdin.seek(0)
                proc=subprocess.Popen([sys.executable,"-m","mizan.worker"],cwd=tmp,env=env,
                                      stdin=stdin,stdout=out,stderr=subprocess.DEVNULL,start_new_session=True)
                start=time.monotonic();beat=start
                try:
                    while proc.poll() is None:
                        if self.stop.is_set():raise Refused("SHUTDOWN","Runner is stopping")
                        if time.monotonic()-start>48:raise Refused("TIME_BUDGET","Worker exceeded its deadline")
                        if time.monotonic()-beat>min(4,b.lease/3):
                            b.heartbeat(t);beat=time.monotonic()
                        time.sleep(.035)
                    require(proc.returncode==0,"WORKER_EXIT","Isolated process terminated")
                    out.seek(0);raw=out.read((8<<20)+1)
                    require(0<len(raw)<=8<<20,"OUTPUT_LIMIT","Invalid worker output length")
                    msg=strict_load(raw)
                    if not msg["ok"]:raise Refused(msg["code"],msg["detail"])
                finally:
                    if proc.poll() is None:
                        try:os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                    proc.wait()
        b.heartbeat(t)
        return verify_output(t["kind"],payload,prof,msg["value"])
