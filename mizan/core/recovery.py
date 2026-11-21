"""Verify a signed backup using an already trusted local root, before restoring to a new path."""
import base64,contextlib,os,shutil,sqlite3,tempfile,uuid
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from .artifacts import DOMAIN,sync_directory
from .contract import strict_load,canonical,digest,require,Refused
from .store import connect,Broker


def validate_backup(broker,source,expected_heads=None):
    src=Path(source)
    env=strict_load((src/"backup.json").read_bytes());meta=env["meta"];seal=env["seal"];body=seal["body"]
    want={"tenant":"system","kind":"backup","object":digest(meta),"context":meta["generation"],"schema":"1","key_id":broker.cas.key_id}
    require(body==want,"BACKUP_SCOPE","Unexpected backup manifest or signing root")
    try:broker.cas.public.verify(base64.b64decode(seal["signature"],validate=True),DOMAIN+canonical(body))
    except (InvalidSignature,ValueError) as exc:raise Refused("BAD_SIGNATURE","Backup signature mismatch") from exc
    require(digest((src/"state.db").read_bytes())==meta["db"],"BACKUP_DB","Snapshot bytes changed")
    for obj in meta["objects"]:
        broker.cas.path(obj)
        f=src/"objects"/obj
        require(f.is_file() and not f.is_symlink() and digest(f.read_bytes())==obj,"BACKUP_BLOB","Snapshot missing a verified artifact")
    if expected_heads is not None:
        for tenant,head in expected_heads.items():
            require(tuple(meta["heads"].get(tenant,[]))==tuple(head),"HISTORY_GAP","Backup differs from the provided trusted audit head")
    with contextlib.closing(sqlite3.connect(f"file:{(src/'state.db').absolute().as_posix()}?mode=ro&immutable=1",uri=True)) as db:
        require(db.execute("PRAGMA integrity_check").fetchone()[0]=="ok" and not db.execute("PRAGMA foreign_key_check").fetchall(),
                "BACKUP_INTEGRITY","Snapshot integrity check failed")
    return meta


def restore(broker,source,target,expected_heads=None,accepted_gap=False):
    src,dest=Path(source),Path(target).resolve()
    require(not dest.exists(),"DEST_EXISTS","Restore requires a fresh directory")
    dest.parent.mkdir(parents=True,exist_ok=True)
    with broker.lock:
        meta=validate_backup(broker,src,expected_heads or broker.verify_audit())
        generation=uuid.uuid4().hex
        stage=Path(tempfile.mkdtemp(dir=dest.parent,prefix=".mizan-restore-"))
        try:
            shutil.copytree(src/"objects",stage/"objects")
            shutil.copytree(broker.root/"keys",stage/"keys")
            shutil.copyfile(src/"state.db",stage/"state.db")
            with contextlib.closing(connect(stage/"state.db")) as conn:
                conn.execute("UPDATE meta SET v=? WHERE k='generation'",(generation,))
            restored=Broker(stage)
            try:
                with restored.tx() as db:restored.log(db,"system","restore",parent=meta["generation"],backup_digest=digest(meta),accepted_history_gap=bool(accepted_gap))
                restored.verify_audit()
            finally:restored.close()
            os.rename(stage,dest);sync_directory(dest.parent)
        finally:
            if stage.exists():shutil.rmtree(stage)
    return generation
