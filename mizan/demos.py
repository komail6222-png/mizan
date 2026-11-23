"""Build the public exhibit strictly from accepted results of the local execution path."""
import base64,copy,json,platform,time
from pathlib import Path
from . import examples
from .core.contract import canonical,digest,require
from .core.store import Broker
from .service import initialize,Runner,drain

def exhibit_html(packet,product=None):
    """The self-verifying exhibit page for a signed packet. The browser re-checks every receipt it displays."""
    web=Path(__file__).parent/"web"
    template=(web/"template.html").read_text(encoding="utf-8")
    # The typeface is embedded so the page stays self-contained and makes no network request.
    template=template.replace("%%FONT_WOFF2%%",base64.b64encode((web/"fonts"/"ReadexPro-subset.woff2").read_bytes()).decode())
    encoded=json.dumps(packet,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    encoded=encoded.replace("&","\\u0026").replace("<","\\u003c").replace(">","\\u003e")
    page=template.replace("%%DEMO_DATA%%",encoded)
    if product:
        page=page.replace('<html lang="en">','<html lang="en" data-product="'+product.upper()+'">')
        require('data-product="'+product.upper()+'"' in page,"TEMPLATE","Missing exhibit root")
        page=page.replace("<title>MIZAN","<title>"+product.upper()+" | MIZAN")
    return page


def build(root,output):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    b=Broker(root);tenant="demo-"+digest([examples.profile(k) for k in ("sanad","basira","rasid")])[:12];actor="fixture-runner";initialize(b,tenant);runner=Runner(b)
    try:
        energy=examples.energy();neutral=copy.deepcopy(energy);neutral["planning"]["risk"]=0
        items=[("evidence","sanad",examples.procurement()),("crack","basira",examples.inspection()),
               ("intact","basira",examples.inspection(defect=None)),("scarce","basira",examples.inspection(4)),
               ("energy","rasid",energy),("neutral","rasid",neutral),("oracle","rasid",examples.peak_oracle())]
        outputs={};receipts=[]
        for label,kind,p in items:
            start=time.perf_counter()
            # Different demo qualifications get distinct operation keys on a changed configuration.
            key="fixture-v1-"+label+"-"+digest(p)
            j=b.submit(tenant,actor,kind,key,p)
            drain(b,runner,tenant,actor,j)
            r=b.get(tenant,actor,j);require(r["state"]=="SUCCEEDED","DEMO_FAILED",str(r.get("reason")))
            outputs[label]=r["result"]
            row=b.db.execute("SELECT payload,profile FROM jobs WHERE id=?",(j,)).fetchone()
            receipts.append({"fixture":label,"job":j,"epoch":r["epoch"],"deployment":r["rev"],"tenant":tenant,
                             "input_digest":row["payload"],"profile_digest":row["profile"],"seal":r["receipt"],
                             "generation":b.result_generation(tenant,j),
                             "result_digest":r["receipt"]["body"]["object"],"request_roundtrip_ms":round((time.perf_counter()-start)*1e3,2)})
        meta={"scope":"Deterministic generated examples, local CPU, no automatic defect PASS or grid actuation.",
              "python":platform.python_version(),"sqlite":b.db.execute("SELECT sqlite_version()").fetchone()[0],
              "public_key_b64":base64.b64encode(b.cas.public.public_bytes_raw()).decode(),"key_fingerprint":b.cas.key_id,
              "receipts":receipts,"audit_heads":b.verify_audit(),"created_unix":int(time.time())}
        (out/"demo_results.json").write_bytes(canonical({"run":meta,"results":outputs}))
        (out/"MIZAN_Demo.html").write_text(exhibit_html({"run":meta,"results":outputs}),encoding="utf-8")
        return meta
    finally:b.close()
