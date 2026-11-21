"""A single local broker multiplexes short transactions over SQLite WAL (Linux/macOS)."""
import base64
import contextlib
import fcntl
import json
import os
import secrets
import shutil
import sqlite3 as sqlite
import threading
import time
import uuid
from pathlib import Path
from .artifacts import Artifacts, install
from .contract import canonical, digest, require, Refused

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deployments(tenant TEXT,subject TEXT,rev INTEGER NOT NULL,seal TEXT NOT NULL,
    state TEXT NOT NULL,PRIMARY KEY(tenant,subject));
CREATE TABLE IF NOT EXISTS versions(tenant TEXT,subject TEXT,rev INTEGER,seal TEXT NOT NULL,
    PRIMARY KEY(tenant,subject,rev));
CREATE TABLE IF NOT EXISTS revoked(tenant TEXT, object TEXT, PRIMARY KEY(tenant,object));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, tenant TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,
    ikey TEXT NOT NULL, payload TEXT NOT NULL,rev INTEGER NOT NULL,profile TEXT NOT NULL,state TEXT NOT NULL,
    epoch INTEGER NOT NULL DEFAULT 0,cancel INTEGER NOT NULL DEFAULT 0,expires REAL NOT NULL DEFAULT 0,
    arrival INTEGER NOT NULL, result TEXT,reason TEXT, generation TEXT, UNIQUE(tenant,actor,kind,ikey));
CREATE INDEX IF NOT EXISTS queue_state ON jobs(state,arrival);
CREATE TABLE IF NOT EXISTS queue_turns(tenant TEXT PRIMARY KEY,turn INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit(tenant TEXT,seq INTEGER,prev TEXT NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,
    PRIMARY KEY(tenant,seq));
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TABLE IF NOT EXISTS witness_receipts(tenant TEXT, generation TEXT, seq INTEGER, receipt TEXT NOT NULL,
    PRIMARY KEY(tenant,generation,seq));
"""
SCHEMA_VERSION = "3"
RETRY_BUDGET = 3

def connect(path):
    ver = sqlite.sqlite_version_info
    if not (ver >= (3,51,3) or ver[:2] == (3,50) and ver[2] >= 7 or ver[:2] == (3,44) and ver[2] >= 6):
        try:
            import pysqlite3 as backend
        except ImportError as exc:
            raise Refused("SQLITE_PATCH", "Install the supplied SQLite wheel or use a fixed SQLite release") from exc
        ver = backend.sqlite_version_info
        require(ver >= (3,51,3) or ver[:2] == (3,50) and ver[2]>=7 or ver[:2] == (3,44) and ver[2]>=6,
                "SQLITE_PATCH","SQLite lacks the WAL-reset fix")
    else:
        backend = sqlite
    db = backend.connect(str(path), timeout=5, isolation_level=None, check_same_thread=False)
    db.row_factory = backend.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


class Broker:
    def __init__(self, root, *, clock=time.time, lease=30, max_queue=64, max_running=1, disk_floor=32<<20):
        self.root = Path(root).resolve()
        self.root.mkdir(exist_ok=True,parents=True,mode=0o700)
        self.clock,self.lease,self.max_queue,self.max_running = clock,lease,max_queue,max_running
        self.disk_floor = disk_floor
        self.lock = threading.RLock()
        self.host = open(self.root / "broker.lock", "a+b")
        try:
            fcntl.flock(self.host, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.host.close()
            raise Refused("BROKER_IN_USE","Only one writer broker can own this store") from exc
        try:
            self.cas = Artifacts(self.root)
            self.db = connect(self.root/"state.db")
            require(self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0]=="wal","WAL","WAL mode unavailable")
            require(self.db.execute("PRAGMA synchronous").fetchone()[0]==2,"DURABILITY","FULL sync unavailable")
            require(self.db.execute("PRAGMA quick_check").fetchone()[0]=="ok","STORE_CORRUPT","SQLite quick_check failed")
            self.db.executescript(SCHEMA)
            with self.tx() as db:
                db.execute("INSERT OR IGNORE INTO meta VALUES('generation',?)",(uuid.uuid4().hex,))
                self.generation = db.execute("SELECT v FROM meta WHERE k='generation'").fetchone()[0]
                # Stable across restores (the generation changes, the store identity does not); used by the witness.
                db.execute("INSERT OR IGNORE INTO meta VALUES('store_id',?)",(uuid.uuid4().hex,))
                self.store_id = db.execute("SELECT v FROM meta WHERE k='store_id'").fetchone()[0]
                self.verify_audit()
                self._migrate(db)
                for row in db.execute("SELECT id,tenant,epoch FROM jobs WHERE state='RUNNING'").fetchall():
                    retry=row["epoch"]<RETRY_BUDGET
                    db.execute("UPDATE jobs SET state=?,reason=?,epoch=epoch+1 WHERE id=?",
                               ("QUEUED" if retry else "FAILED","RESTART_FENCED" if retry else "RETRY_BUDGET_EXHAUSTED",row["id"]))
                    self.log(db,row["tenant"],"restart_fenced",job=row["id"])
        except BaseException:
            if hasattr(self,"db"):self.db.close()
            self.host.close()
            raise

    def _migrate(self, db):
        """Expand-only schema migration inside the startup transaction (2.1 stores lack jobs.generation)."""
        columns={r["name"] for r in db.execute("PRAGMA table_info(jobs)")}
        row=db.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        previous=row[0] if row else ("1" if "generation" not in columns else SCHEMA_VERSION)
        if "generation" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN generation TEXT")
        db.execute("INSERT INTO meta VALUES('schema_version',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(SCHEMA_VERSION,))
        if previous!=SCHEMA_VERSION:
            self.log(db,"system","schema_migrated",previous=previous,current=SCHEMA_VERSION)

    def close(self):
        with self.lock:
            self.db.close()
            self.host.close()

    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:self.db.execute("ROLLBACK")
                raise

    def log(self, db, tenant, action, **fields):
        last = db.execute("SELECT seq,hash FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 1",(tenant,)).fetchone()
        seq,prev=(last[0]+1,last[1]) if last else (1,"0"*64)
        body = {"action":action,"fields":fields,"time_ns":time.time_ns(),"generation":self.generation}
        h=digest(["mizan.audit.v1",tenant,seq,prev,body])
        db.execute("INSERT INTO audit VALUES(?,?,?,?,?)",(tenant,seq,prev,canonical(body).decode(),h))

    def verify_audit(self):
        heads={}
        with self.lock:
            for row in self.db.execute("SELECT * FROM audit ORDER BY tenant,seq"):
                t=row["tenant"]; seq,prev=heads.get(t,(0,"0"*64))
                want=digest(["mizan.audit.v1",t,seq+1,prev,json.loads(row["body"])])
                require(row["seq"]==seq+1 and row["prev"]==prev and secrets.compare_digest(want,row["hash"]),
                        "AUDIT_DIVERGED","Audit chain verification failed")
                heads[t]=(seq+1,want)
        return heads

    def audit_entries(self, tenant, after=0):
        """Audit rows after a sequence number, in the exact form the chain hash covers."""
        with self.lock:
            return [{"seq":r["seq"],"prev":r["prev"],"body":json.loads(r["body"]),"hash":r["hash"]}
                    for r in self.db.execute("SELECT * FROM audit WHERE tenant=? AND seq>? ORDER BY seq",(tenant,after))]

    def promote(self, tenant, subject, spec, expected, *, actor="local-operator"):
        with self.tx() as db:
            obj=self.cas.put(spec)
            require(not db.execute("SELECT 1 FROM revoked WHERE tenant=? AND object=?",(tenant,obj)).fetchone(),
                    "REVOKED","Cannot activate a revoked bundle")
            current=db.execute("SELECT rev FROM deployments WHERE tenant=? AND subject=?",(tenant,subject)).fetchone()
            rev=current[0] if current else 0
            require(rev==expected,"STALE_REVISION","Another deployment has already committed")
            seal=self.cas.seal(tenant,"profile",obj,subject)
            self.cas.open(seal,tenant,"profile",subject)
            db.execute("INSERT INTO deployments VALUES(?,?,?,?,?) ON CONFLICT(tenant,subject) DO UPDATE SET rev=excluded.rev,seal=excluded.seal,state=excluded.state",
                       (tenant,subject,rev+1,canonical(seal).decode(),"READY"))
            db.execute("INSERT INTO versions VALUES(?,?,?,?)",(tenant,subject,rev+1,canonical(seal).decode()))
            self.log(db,tenant,"promoted",subject=subject,rev=rev+1,object=obj,actor=actor)
        return rev+1

    def rollback(self,tenant,subject,target_revision,expected,permitted_engine):
        with self.lock:
            r=self.db.execute("SELECT seal FROM versions WHERE tenant=? AND subject=? AND rev=?",(tenant,subject,target_revision)).fetchone()
            require(r is not None,"UNKNOWN_TARGET","No retained signed revision for this subject")
            spec=self.cas.open(json.loads(r[0]),tenant,"profile",subject)
            require(spec.get("engine")==permitted_engine,"INCOMPATIBLE","Rollback target needs a different runtime")
            return self.promote(tenant,subject,spec,expected,actor="local-rollback-to-rev-"+str(target_revision))

    def revoke(self, tenant, object_id):
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO revoked VALUES(?,?)",(tenant,object_id))
            for d in db.execute("SELECT subject,seal FROM deployments WHERE tenant=?",(tenant,)).fetchall():
                if json.loads(d["seal"])["body"]["object"]==object_id:
                    db.execute("UPDATE deployments SET state='SUSPENDED',rev=rev+1 WHERE tenant=? AND subject=?",(tenant,d["subject"]))
            self.log(db,tenant,"revoked",object=object_id)

    def submit(self,tenant,actor,kind,key,payload):
        require(isinstance(key,str) and 1<=len(key)<=128,"IDEMPOTENCY","Use a bounded idempotency key")
        raw=canonical(payload)
        require(len(raw)<=4<<20,"INPUT_LIMIT","Job input exceeds 4 MiB")
        ph=digest(raw)
        with self.tx() as db:
            row=db.execute("SELECT id,payload FROM jobs WHERE tenant=? AND actor=? AND kind=? AND ikey=?",(tenant,actor,kind,key)).fetchone()
            if row:
                require(row["payload"]==ph,"IDEMPOTENCY_CONFLICT","Key reused with a different payload")
                return row["id"]
            require(shutil.disk_usage(self.root).free>self.disk_floor,"DISK_RESERVE","Bulk admissions paused")
            require(db.execute("SELECT count(*) FROM jobs WHERE state IN ('QUEUED','RUNNING')").fetchone()[0]<self.max_queue,"QUEUE_FULL","Queue is full")
            d=db.execute("SELECT * FROM deployments WHERE tenant=? AND subject=?",(tenant,kind)).fetchone()
            require(d and d["state"]=="READY","NOT_READY","No qualified local deployment")
            profile=json.loads(d["seal"]);self.cas.open(profile,tenant,"profile",kind)
            self.cas.put(payload); jid=uuid.uuid4().hex
            db.execute("INSERT INTO jobs(id,tenant,actor,kind,ikey,payload,rev,profile,state,arrival) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (jid,tenant,actor,kind,key,ph,d["rev"],profile["body"]["object"],"QUEUED",time.time_ns()))
            self.log(db,tenant,"submitted",job=jid,kind=kind,revision=d["rev"])
            return jid

    def claim(self):
        with self.tx() as db:
            now=self.clock()
            for r in db.execute("SELECT id,tenant,epoch FROM jobs WHERE state='RUNNING' AND expires<=?",(now,)).fetchall():
                retry=r["epoch"]<RETRY_BUDGET
                db.execute("UPDATE jobs SET state=?,reason=? WHERE id=?",
                           ("QUEUED" if retry else "FAILED","EXPIRED" if retry else "RETRY_BUDGET_EXHAUSTED",r["id"]))
                self.log(db,r["tenant"],"lease_expired",job=r["id"],epoch=r["epoch"])
            if db.execute("SELECT count(*) FROM jobs WHERE state='RUNNING'").fetchone()[0]>=self.max_running:return None
            r=db.execute("""SELECT j.* FROM jobs j LEFT JOIN queue_turns q ON q.tenant=j.tenant
                WHERE j.state='QUEUED' ORDER BY COALESCE(q.turn,0),j.arrival,j.id LIMIT 1""").fetchone()
            if not r:return None
            turn=db.execute("SELECT COALESCE(MAX(turn),0)+1 FROM queue_turns").fetchone()[0]
            db.execute("INSERT INTO queue_turns VALUES(?,?) ON CONFLICT(tenant) DO UPDATE SET turn=excluded.turn",(r["tenant"],turn))
            epoch=r["epoch"]+1
            db.execute("UPDATE jobs SET state='RUNNING',epoch=?,expires=? WHERE id=?",(epoch,now+self.lease,r["id"]))
            self.log(db,r["tenant"],"claimed",job=r["id"],epoch=epoch)
            return dict(id=r["id"],tenant=r["tenant"],kind=r["kind"],payload=r["payload"],profile=r["profile"],rev=r["rev"],
                        epoch=epoch,cancel=r["cancel"],generation=self.generation)

    def _fence(self,db,t):
        r=db.execute("SELECT * FROM jobs WHERE id=? AND tenant=?",(t["id"],t["tenant"])).fetchone()
        require(t["generation"]==self.generation and r and r["state"]=="RUNNING" and r["epoch"]==t["epoch"]
                and r["cancel"]==t["cancel"] and r["expires"]>self.clock() and r["payload"]==t["payload"]
                and r["profile"]==t["profile"] and r["rev"]==t["rev"] and r["kind"]==t["kind"],
                "STALE_ATTEMPT","Attempt was fenced, cancelled, or expired")
        return r

    def heartbeat(self,t):
        with self.tx() as db:
            self._fence(db,t)
            db.execute("UPDATE jobs SET expires=? WHERE id=?",(self.clock()+self.lease,t["id"]))

    def cancel(self,tenant,actor,jid):
        with self.tx() as db:
            r=self._owned(db,tenant,actor,jid)
            if r["state"] not in ("RUNNING","QUEUED"):return r["state"]
            db.execute("UPDATE jobs SET state='CANCELLED',cancel=cancel+1 WHERE id=?",(jid,))
            self.log(db,tenant,"cancelled",job=jid)
            return "CANCELLED"

    def fail(self,t,reason):
        with self.tx() as db:
            self._fence(db,t)
            db.execute("UPDATE jobs SET state='FAILED',reason=? WHERE id=?",(reason,t["id"]))
            self.log(db,t["tenant"],"failed",job=t["id"],reason=reason)

    def publish(self,t,verified):
        # Bytes become durable before the DB reference. Serialize orphan collection with staging.
        with self.lock:
            obj=self.cas.put(verified)
            ctx=digest([t["id"],t["payload"],t["profile"],t["rev"],t["epoch"],self.generation])
            seal=self.cas.seal(t["tenant"],"result",obj,ctx)
            with self.tx() as db:
                self._fence(db,t)
                d=db.execute("SELECT * FROM deployments WHERE tenant=? AND subject=?",(t["tenant"],t["kind"])).fetchone()
                require(d and d["state"]=="READY" and d["rev"]==t["rev"],"DEPLOYMENT_CHANGED","Deployment changed before publication")
                require(not db.execute("SELECT 1 FROM revoked WHERE tenant=? AND object=?",(t["tenant"],t["profile"])).fetchone(),
                        "REVOKED","Pinned profile was revoked")
                self.cas.open(seal,t["tenant"],"result",ctx)
                db.execute("UPDATE jobs SET state='SUCCEEDED',result=?,generation=? WHERE id=?",
                           (canonical(seal).decode(),self.generation,t["id"]))
                self.log(db,t["tenant"],"published",job=t["id"],result=obj,context=ctx,epoch=t["epoch"])
        return obj

    def _owned(self,db,t,a,j):
        r=db.execute("SELECT * FROM jobs WHERE id=? AND tenant=? AND actor=?",(j,t,a)).fetchone()
        require(r is not None,"NOT_FOUND","Job not found")
        return r

    def get(self,t,a,j):
        with self.lock:
            r=self._owned(self.db,t,a,j)
            out={k:r[k] for k in ("id","kind","state","rev","epoch","reason")}
            if r["result"]:
                seal=json.loads(r["result"])
                generation=self.result_generation(t,j,r)
                out["result"]=self.cas.open(seal,t,"result",digest([j,r["payload"],r["profile"],r["rev"],r["epoch"],generation]))
                out["receipt"]=seal
                # Everything an offline verifier needs to recompute the signed context (python -m mizan verify).
                out["provenance"]={"tenant":t,"input_digest":r["payload"],"profile_digest":r["profile"],"generation":generation,
                                   "key_fingerprint":self.cas.key_id,
                                   "public_key_b64":base64.b64encode(self.cas.public.public_bytes_raw()).decode()}
            return out

    def result_generation(self,tenant,jid,row=None):
        """Generation that signed the result. Stored on the job since schema 2; 2.1 rows fall back to the audit log."""
        with self.lock:
            row=row or self.db.execute("SELECT generation FROM jobs WHERE id=? AND tenant=?",(jid,tenant)).fetchone()
            if row is not None and row["generation"]:return row["generation"]
            return self._result_generation(tenant,jid)

    def _result_generation(self,tenant,jid):
        for row in self.db.execute("SELECT body FROM audit WHERE tenant=? ORDER BY seq DESC",(tenant,)):
            b=json.loads(row[0])
            if b["action"]=="published" and b["fields"]["job"]==jid:return b["generation"]
        raise Refused("AUDIT_MISSING","Result has no publication event")

    def referenced(self):
        refs=set()
        for r in self.db.execute("SELECT payload,profile,result FROM jobs"):
            refs.update([r[0],r[1]])
            if r[2]:refs.add(json.loads(r[2])["body"]["object"])
        for r in self.db.execute("SELECT seal FROM deployments"):refs.add(json.loads(r[0])["body"]["object"])
        for r in self.db.execute("SELECT body FROM audit"):
            b=json.loads(r[0])
            if b["action"]=="promoted":refs.add(b["fields"]["object"])
        return refs

    def collect(self,grace=3600):
        with self.lock:
            live=self.referenced();removed=[]
            for p in self.cas.blobs.iterdir():
                if len(p.name)==64 and p.name not in live and time.time()-p.stat().st_mtime>grace:
                    p.unlink();removed.append(p.name)
            return removed

    def backup(self,target):
        target=Path(target)
        require(not target.exists(),"BACKUP_EXISTS","Choose a new backup directory")
        target.mkdir(parents=True)
        with self.lock:
            snapshot=connect(target/"state.db")
            try:self.db.backup(snapshot)
            finally:snapshot.close()
            objects=sorted(self.referenced());(target/"objects").mkdir()
            for obj in objects:
                self.cas.read(obj);shutil.copyfile(self.cas.path(obj),target/"objects"/obj)
            meta=dict(db=digest((target/"state.db").read_bytes()),objects=objects,heads=self.verify_audit(),generation=self.generation)
            body=self.cas.put(meta)
            install(target/"backup.json",canonical({"meta":meta,"seal":self.cas.seal("system","backup",body,self.generation)}))
        return target

    def checkpoint(self):
        with self.lock:
            busy,frames,done=self.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            return dict(busy=busy,frames=frames,checkpointed=done,free_bytes=shutil.disk_usage(self.root).free)
