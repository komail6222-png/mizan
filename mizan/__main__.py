"""Run from the repository root: python -m mizan --help."""
import argparse,contextlib,json,os,platform,secrets,shutil,sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(prog="python -m mizan")
    parser.add_argument("--data",default=os.getenv("MIZAN_DATA","var/mizan"))
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("demo");p.add_argument("--out",default="reports/demo")
    p=sub.add_parser("serve");p.add_argument("--host",default="127.0.0.1");p.add_argument("--port",type=int,default=7860);p.add_argument("--public-demo",action="store_true")
    p=sub.add_parser("run");p.add_argument("product",choices=["sanad","basira","rasid"]);p.add_argument("input");p.add_argument("--key");p.add_argument("--out")
    p=sub.add_parser("examples");p.add_argument("--out",default="examples")
    p=sub.add_parser("approve-document",help="Approve a reviewed transcript; without --level it only shows the review")
    p.add_argument("document");p.add_argument("--level",type=int,choices=[1,2,3])
    p.add_argument("--acknowledge",action="append",default=[],metavar="PAGE:START",help="Declare one shown uncovered line non-evidential")
    p.add_argument("--acknowledge-eligible",action="store_true",help="Acknowledge every shown line marked acknowledgeable")
    sub.add_parser("upgrade",help="Move deployments to this release's engines, preserving approvals")
    p=sub.add_parser("rollback");p.add_argument("product",choices=["sanad","basira","rasid"]);p.add_argument("--revision",type=int,required=True);p.add_argument("--expected-current",type=int,required=True)
    p=sub.add_parser("backup");p.add_argument("directory")
    p=sub.add_parser("restore");p.add_argument("backup_directory");p.add_argument("new_directory")
    p.add_argument("--accept-history-gap",action="store_true",help="Restore a backup older than the current history; the audit log and the witness record the gap")
    p=sub.add_parser("hf");p.add_argument("action",choices=["pins","fetch","compare"]);p.add_argument("product",choices=["sanad","basira","rasid"]);p.add_argument("--cache",default="var/hf")
    p=sub.add_parser("verify",help="Verify a run --out file or an exhibit packet offline");p.add_argument("file");p.add_argument("--expected-key",help="SHA-256 fingerprint obtained through a trusted channel")
    p=sub.add_parser("mcp",help="Serve MIZAN to AI agents over the Model Context Protocol (stdio)")
    p.add_argument("--budget",type=int,default=25,help="Maximum new jobs per agent session");p.add_argument("--witness",help="Witness directory for audit_status")
    p=sub.add_parser("explain",help="Explain a verified run --out file in Arabic and English, with executed counterfactuals")
    p.add_argument("file");p.add_argument("--expected-key");p.add_argument("--lang",choices=["en","ar","both"],default="both")
    p=sub.add_parser("witness-init",help="Create an audit witness (separate key and state) in a directory");p.add_argument("directory")
    p=sub.add_parser("anchor",help="Send unwitnessed audit entries to a witness and keep its receipts");p.add_argument("--witness",required=True)
    sub.add_parser("doctor")
    p=sub.add_parser("audit",help="Verify the local audit chain; with --witness or --witness-key also report witnessed coverage")
    p.add_argument("--witness",help="Witness directory (live check)");p.add_argument("--witness-key",help="Witness public key, base64 (stored receipts only)")
    args=parser.parse_args()
    from .core.contract import Refused,canonical,strict_load,require
    result=None
    try:
        if args.command=="mcp":
            from .agent import serve
            from .core.store import Broker
            sys.stdin.reconfigure(encoding="utf-8");sys.stdout.reconfigure(encoding="utf-8")
            with contextlib.closing(Broker(args.data)) as broker:
                if args.witness:
                    from .core.witness import Witness
                    with contextlib.closing(Witness(args.witness)) as w:serve(broker,budget=args.budget,witness=w)
                else:serve(broker,budget=args.budget)
            return
        if args.command=="serve":
            import uvicorn
            from .api import app_factory
            uvicorn.run(app_factory(args.data,True if args.public_demo else None),host=args.host,port=args.port,log_level="warning");return
        if args.command=="explain":
            from .explain import explain
            from .receipts import verify_job_output
            from .core.contract import digest
            packet=strict_load(Path(args.file).read_bytes());check=verify_job_output(packet,args.expected_key)
            def stored(key):
                # Read-only lookup in the local content-addressed store; no broker lock is taken.
                f=Path(args.data)/"objects"/key
                if f.is_file():
                    raw=f.read_bytes();require(digest(raw)==key,"OBJECT_CHANGED","Stored object digest mismatch");return strict_load(raw)
                return None
            prov=packet["provenance"];payload,profile=stored(prov["input_digest"]),stored(prov["profile_digest"])
            result=explain(packet["kind"],packet["result"],payload,profile)
            result["derived_from"]={"job":check["job"],"result_digest":check["result_digest"],"key_fingerprint":check["key_fingerprint"]}
            if args.lang!="both":result.pop("ar" if args.lang=="en" else "en")
        elif args.command=="witness-init":
            from .core.witness import Witness
            with contextlib.closing(Witness(args.directory)) as w:
                result={"witness":str(w.root),"key_fingerprint":w.key_id,"public_key_b64":w.public_b64,
                        "note":"Keep this directory outside the broker's data directory, ideally on another host and account."}
        elif args.command=="verify":
            from .receipts import verify_any
            result=verify_any(strict_load(Path(args.file).read_bytes()),args.expected_key)
        elif args.command=="hf":
            from .models import PINS,fetch,experiment
            result=PINS[args.product] if args.action=="pins" else (str(fetch(args.product,args.cache)) if args.action=="fetch" else experiment(args.product,args.cache))
        elif args.command=="doctor":
            import sqlite3,importlib.util
            result={"python":platform.python_version(),"sqlite_stdlib":sqlite3.sqlite_version,"platform":platform.system(),
                    "disk_free_gib":round(shutil.disk_usage(".").free/2**30,2),
                    "optional_installed":{m:bool(importlib.util.find_spec(m)) for m in ["torch","transformers","huggingface_hub","chronos"]}}
        elif args.command=="demo":
            from .demos import build
            result=build(args.data,args.out)
        elif args.command=="examples":
            from . import examples
            out=Path(args.out);out.mkdir(exist_ok=True,parents=True)
            for k,f in [("sanad",examples.procurement),("basira",examples.inspection),("rasid",examples.energy),("rasid_oracle",examples.peak_oracle)]:
                (out/(k+".json")).write_bytes(canonical(f()))
            result={"directory":str(out)}
        else:
            from .core.store import Broker
            from .service import initialize,Runner
            with contextlib.closing(Broker(args.data)) as broker:
                initialize(broker,"local")
                if args.command=="rollback":
                    from .examples import profile
                    result={"deployment_revision":broker.rollback("local",args.product,args.revision,args.expected_current,profile(args.product)["engine"])}
                elif args.command=="backup":result={"backup":str(broker.backup(args.directory))}
                elif args.command=="restore":
                    from .core.recovery import restore
                    heads=None
                    if args.accept_history_gap:
                        # The heads come from the signed backup manifest, which restore verifies before trusting them.
                        meta=strict_load((Path(args.backup_directory)/"backup.json").read_bytes())["meta"]
                        heads={t:tuple(v) for t,v in meta["heads"].items()}
                    result={"generation":restore(broker,args.backup_directory,args.new_directory,heads,accepted_gap=args.accept_history_gap)}
                    if args.accept_history_gap:result["next"]="Run anchor against the witness so it records the recovery gap"
                elif args.command=="audit":
                    result={"heads":broker.verify_audit(),"wal":broker.checkpoint()}
                    if args.witness or args.witness_key:
                        from .core.anchoring import witness_report
                        if args.witness:
                            from .core.witness import Witness
                            with contextlib.closing(Witness(args.witness)) as w:result["witness"]=witness_report(broker,w)
                        else:result["witness"]=witness_report(broker,public_b64=args.witness_key)
                elif args.command=="anchor":
                    from .core.anchoring import anchor
                    from .core.witness import Witness
                    with contextlib.closing(Witness(args.witness)) as w:result=anchor(broker,w)
                elif args.command=="upgrade":
                    from .service import upgrade
                    result=upgrade(broker,"local")
                elif args.command=="approve-document":
                    from .sanad.engine import review_document,check_profile
                    doc=strict_load(Path(args.document).read_bytes());review=review_document(doc)
                    lines=review["lines"];chosen=[]
                    for ref in args.acknowledge:
                        page,_,start=ref.partition(":")
                        match=[l for l in lines if f"{l['page']}:{l['start']}"==f"{page}:{start}"]
                        require(match,"ACKNOWLEDGE","No shown line at "+ref)
                        require(match[0]["acknowledgeable"],"NOT_ACKNOWLEDGEABLE",
                                "Line names a scored attribute or a measurable value; correct the transcript instead: "+ref)
                        chosen.append(match[0])
                    if args.acknowledge_eligible:chosen+= [l for l in lines if l["acknowledgeable"] and l not in chosen]
                    result={"document":review["source"],"lines_needing_review":lines}
                    if args.level is not None:
                        with broker.lock:
                            dep=broker.db.execute("SELECT * FROM deployments WHERE tenant='local' AND subject='sanad'").fetchone()
                        profile=broker.cas.open(json.loads(dep["seal"]),"local","profile","sanad");check_profile(profile)
                        acks=[{k:l[k] for k in ("page","start","end","quote_sha256")} for l in chosen]
                        profile["approved_sources"][review["source"]]={"level":args.level,"acknowledged":acks} if acks else args.level
                        result.update(authority=args.level,acknowledged=len(acks),
                                      unacknowledged_lines=sum(1 for l in lines if l not in chosen),
                                      revision=broker.promote("local","sanad",profile,dep["rev"],actor="local-operator-approval"))
                    elif chosen:
                        raise Refused("LEVEL_REQUIRED","Pass --level to record acknowledgements in a signed approval")
                elif args.command=="run":
                    from .service import drain
                    payload=strict_load(Path(args.input).read_bytes());r=Runner(broker)
                    j=broker.submit("local","operator",args.product,args.key or secrets.token_hex(12),payload)
                    result=drain(broker,r,"local","operator",j)
                    hints={"PROFILE_UPGRADE_REQUIRED":" (this store predates the current engine; run: python -m mizan upgrade)"}
                    require(result["state"]=="SUCCEEDED","JOB_FAILED",str(result["reason"])+hints.get(result["reason"],""))
                    if args.out:
                        Path(args.out).write_bytes(canonical(result))
                        result={"job":j,"state":result["state"],"output":args.out}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (Refused,FileNotFoundError,KeyError,ValueError) as e:
        print(json.dumps({"error":getattr(e,"code","INPUT"),"detail":str(e)[:220]}),file=sys.stderr)
        raise SystemExit(2) from e

if __name__=="__main__":main()
