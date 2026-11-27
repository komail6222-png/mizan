"""Render BASIRA on real images (VisA) from reports/visa/basira.json: a summary page and a per-class page.

    .venv/bin/python scripts/build_visa_report.py          # writes reports/visa/index.html and reports/visa/classes.html

The pages reuse the evaluation report's style and helpers (scripts/build_report.py). Every figure, interval and
direction word is computed in the page from the embedded evidence; nothing is typed by hand. A record whose gate did
not pass, or whose repeat run was not identical, is refused.
"""
import argparse
import html
import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan.visa import ATTRIBUTION  # noqa: E402
from scripts.build_report import _page, fail  # noqa: E402

SCORERS = ("descriptor", "dinov2-engine", "dinov2-native")
RULES = ("as_shipped", "full", "full_pac")
LICENCE = "https://creativecommons.org/licenses/by/4.0/"


def _r(v, digits=6):
    return None if v is None else round(float(v), digits)


def _rate(r):
    """[x, n, ci_lo, ci_hi]: counts stay exact; interval ends are rounded to 6 decimals (shown to 0.1 points)."""
    lo, hi = r["ci95"] if r["ci95"] else (None, None)
    return [r["x"], r["n"], _r(lo), _r(hi)]


def _exch(e):
    if not e or e.get("applicable") is False:
        return None
    return [e["observed"], e["n"], _r(e["expected_mean"], 3), e["range95"][0], e["range95"][1], _r(e["p_two_sided"], 4), e["verdict"]]


def _checked(record):
    require = lambda ok, msg: ok or fail(msg)  # noqa: E731
    require(record.get("gate", {}).get("passed") is True, "reports/visa/basira.json: the fidelity gate did not pass")
    require((record.get("repeat_run") or {}).get("identical") is True,
            "reports/visa/basira.json: the repeat run is missing or not identical")
    require(record.get("quick") is False, "reports/visa/basira.json: a quick run is a smoke test, not evidence")
    require(set(SCORERS) <= set(record["pooled"]), "reports/visa/basira.json: a scorer is missing")
    return record


def _cfg(record):
    """The protocol's numbers as the pages state them, read from the record's settings."""
    s = record["settings"]
    conf = s["configurations"]
    return {"as_shipped": [conf["as_shipped"]["reference"], conf["as_shipped"]["calibration"]],
            "full": [conf["full"]["reference"], conf["full"]["calibration"]],
            "alpha": f"{float(Fraction(s['alpha']) * 100):g}%", "delta": f"{float(Fraction(s['delta'])):g}",
            "draws": s["study"]["draws"]}


def summary(record):
    """What the summary page embeds: pooled rates, the exchangeability check, AUROC, paired comparisons, examples."""
    _checked(record)
    splits = record["splits"]
    examples = {k: (None if v is None else [v["class"], v["path"], _r(v["score"], 4), v["jpeg"]]) for k, v in record["examples"].items()}
    env = record["environment"]
    return {
        "classes": [[c, s["type"], s["test_normal"], s["test_anomaly"], s["refused"], s["duplicates_dropped"]] for c, s in splits.items()],
        "pooled": {s: {r: {"fa": _rate(p["false_alert"]), "det": _rate(p["detection"])} for r, p in record["pooled"][s].items()} for s in SCORERS},
        "exch": {s: {r: {"pooled": _exch(e.get("pooled")), "by_type": {t: _exch(v) for t, v in (e.get("by_type") or {}).items()}}
                     for r, e in record["exchangeability"][s].items()} for s in SCORERS},
        "auroc": {s: [_r(record["auroc"][s]["mean"], 4), _r(record["auroc"][s]["mean_ci95"][0], 4), _r(record["auroc"][s]["mean_ci95"][1], 4)] for s in SCORERS},
        "paired": [{"a": p["a"], "b": p["b"], **{m: [_r(p[m]["difference"]), _r(p[m]["ci95"][0]), _r(p[m]["ci95"][1]), p[m]["reading"]]
                                                  for m in ("detection", "false_alert", "auroc")}} for p in record["paired"]],
        "examples": examples,
        "gate": {"fixture": len(record["gate"]["fixture"]["checked"]), "dinov2": len(record["gate"]["dinov2"]["checked"]),
                 "bit_exact": record["gate"]["dinov2"]["bit_exact"]},
        "repeat": record["repeat_run"]["process"], "seal": record["seal_commit"], "digest": record["protocol_digest"],
        "env": {k: env.get(k) for k in ("python", "numpy", "scipy", "pillow", "torch", "cpu")}, "elapsed": record["elapsed_seconds"],
        "cfg": _cfg(record),
    }


def classes_summary(record):
    """What the per-class page embeds: per-class rates for every rule, per-type rates, per-class AUROC and the study."""
    _checked(record)
    splits = record["splits"]
    classes = sorted(splits, key=lambda c: (splits[c]["type"], c))   # grouped by VisA type
    def study_rows(rows):
        return [[r["m"], _r(r["mean_false_alert"]), _r(r["share_above_alpha"], 4), _r(r["theoretical_share_above_alpha"], 4),
                 _r(r["mean_detection"])] for r in rows]
    return {
        "classes": [[c, record["splits"][c]["type"]] for c in classes],
        "per_class": {s: {r: {c: _rate(v["false_alert"]) + _rate(v["detection"]) for c, v in record["per_class"][s][r].items()}
                          for r in RULES} for s in SCORERS},
        "by_type": {s: {r: {t: _rate(v["false_alert"]) + _rate(v["detection"]) for t, v in record["pooled"][s][r]["by_type"].items()}
                        for r in RULES} for s in SCORERS},
        "auroc": {s: {c: [_r(v["auroc"], 4), _r(v["ci95"][0], 4), _r(v["ci95"][1], 4)] for c, v in record["auroc"][s]["per_class"].items()}
                  for s in SCORERS},
        "study": {s: {"pooled": study_rows(record["study"][s]["pooled"]),
                      "per_class": {c: study_rows(v["rows"]) for c, v in record["study"][s]["per_class"].items()}} for s in SCORERS},
        "cfg": _cfg(record),
    }


ATTRIBUTION_HTML = (f'<p class="lede" id="visa-attribution">{html.escape(ATTRIBUTION, quote=False)} '
                    f'Licence: <a href="{LICENCE}">{LICENCE}</a>. Data: <a href="https://github.com/amazon-science/spot-diff">'
                    'github.com/amazon-science/spot-diff</a>.</p>')

COMMON_JS = r"""const N={"descriptor":"Shipped descriptor","dinov2-engine":"DINOv2-small, engine input","dinov2-native":"DINOv2-small, native input"};
const C=D.cfg,R={"as_shipped":`As shipped: ${C.as_shipped[0]} reference, ${C.as_shipped[1]} calibration`,"full":`Full: ${C.full[0]} reference, ${C.full[1]} calibration`,"full_pac":`Full, PAC rule (δ = ${C.delta})`};
const ci=(lo,hi)=>lo===null?"–":`${pct(lo)} to ${pct(hi)}`;
const rate=a=>a[1]?`${fmt(a[0])} of ${fmt(a[1])} · ${pct(a[0]/a[1])}`:"–";
const put=(id,s)=>{document.getElementById(id).textContent=s};"""

SUMMARY_PAGE = r"""<div class="kicker">Release 3.2 · real public images (VisA)</div>
<h1>BASIRA on real images</h1>
<p class="lede" id="visa-lede"></p>
<section id="visa-section">
<div class="card"><h3>Defects caught and false alerts, full configuration</h3><p class="sub" id="visa-head-sub"></p>
<div class="scroll"><table class="fit" id="visa-head"></table></div><p class="note" id="visa-head-note"></p></div>
<div class="card"><h3>Does the false-alert promise hold on real parts?</h3><p class="sub" id="visa-exch-sub"></p>
<div class="scroll"><table id="visa-exch"></table></div>
<h3 class="gap">By VisA type, full configuration</h3>
<div class="scroll"><table id="visa-exch-type"></table></div><p class="note" id="visa-exch-note"></p></div>
<div class="card"><h3>Every configuration</h3><div class="scroll"><table id="visa-rules"></table></div></div>
<div class="card"><h3>Ranking quality</h3><p class="sub" id="visa-auroc-sub"></p>
<div class="scroll"><table class="fit" id="visa-auroc"></table></div></div>
<div class="card"><h3>Scorer comparisons on the same images</h3><p class="sub" id="visa-paired-sub"></p>
<div class="scroll"><table id="visa-paired"></table></div><p class="note" id="visa-paired-note"></p></div>
<div class="card"><h3>Four real examples, chosen by a fixed rule</h3><p class="sub" id="visa-examples-sub"></p>
<div class="examples" id="visa-examples"></div></div>
<h2>Per class</h2>
<p class="lede">Every class, every configuration and the repeated-calibration study: <a href="classes.html">BASIRA on VisA, per class</a>.</p>
<h2>Data</h2>
%%ATTRIBUTION%%
</section>
<p class="foot" id="foot"></p>
</main></div>
<style>.examples{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.examples figure{margin:0}
.examples img,.examples .empty{width:96px;height:96px;border-radius:6px;display:block;border:1px solid var(--border)}.examples .empty{border-style:dashed}
.examples figcaption{font-size:13px;color:var(--text-secondary);margin-top:6px}.examples strong{color:var(--text-primary);display:block}</style>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
%%HELPERS%%
%%COMMON%%
const T=D.classes.reduce((a,c)=>[a[0]+c[2],a[1]+c[3],a[2]+c[4],a[3]+c[5]],[0,0,0,0]);
put("visa-lede",`${D.classes.length} real products from the VisA dataset, each treated as one customer's product line: reference and calibration parts come from normal images only, and decisions are made on the official test split, ${fmt(T[0])} normal and ${fmt(T[1])} defective images${T[2]?` (${fmt(T[2])} refused as duplicates of a reference or calibration image)`:""}. The protocol was sealed at commit ${D.seal.slice(0,12)} before any test image was scored. The fidelity gate reproduced the shipped frozen test exactly (${fmt(D.gate.fixture)} checks) and 3.1's DINOv2 frozen test ${D.gate.bit_exact?"bit for bit":"within tolerance"} (${fmt(D.gate.dinov2)} checks), and the whole run repeated identically in ${D.repeat}.`);
table("visa-head",["Scorer","Defects caught","False alerts"],["descriptor","dinov2-engine","dinov2-native"].map(s=>{const p=D.pooled[s].full;return[N[s],`${rate(p.det)} (${ci(p.det[2],p.det[3])})`,`${rate(p.fa)} (${ci(p.fa[2],p.fa[3])})`]}),[0,1,1]);
put("visa-head-sub",`Pooled over all classes, marginal rule at α = ${C.alpha}, with exact 95% intervals. Each false-alert interval describes these ${D.classes.length} fitted thresholds only, one per class; whether the promise holds is the exchangeability check below.`);
const best=s=>D.pooled[s].full.det[0]/D.pooled[s].full.det[1];
put("visa-head-note",`The shipped descriptor sees each part as the engine does, 96 × 96 greyscale. DINOv2-small on the engine input sees the same image; at native input it sees the original colour photograph, off the engine path. Detection: ${["descriptor","dinov2-engine","dinov2-native"].map(s=>`${N[s]} ${pct(best(s))}`).join(", ")}.`);
const V={"consistent":"Consistent with exchangeability","more":"More false alerts than exchangeability allows","fewer":"Fewer false alerts than exchangeability implies"};
const ex=(e)=>e?[`${fmt(e[0])} of ${fmt(e[1])}`,`${fmt(e[3])} to ${fmt(e[4])}`,V[e[6]]]:["–","–","Not applicable"];
table("visa-exch",["Scorer","Rule","Observed false alerts","Expected range","Reading"],["descriptor","dinov2-engine","dinov2-native"].flatMap(s=>["as_shipped","full","full_pac"].map(r=>[N[s],R[r],...ex(D.exch[s][r].pooled)])),[0,0,1,1,0]);
put("visa-exch-sub","The promise rests on one assumption: test normals behave like the calibration normals. If they do, each rule implies an exact distribution for the number of false alerts; the range is its central 95%.");
const types=Object.keys(D.exch.descriptor.full.by_type);
table("visa-exch-type",["Type","Scorer","Observed","Expected range","Reading"],types.flatMap(t=>["descriptor","dinov2-engine","dinov2-native"].map(s=>[t,N[s],...ex(D.exch[s].full.by_type[t])])),[0,0,1,1,0]);
put("visa-exch-note","Within a class, every test normal shares one fitted threshold, so the per-class rate is itself random; the expected range accounts for that (Beta-Binomial per class, exact convolution over classes). A reading of more false alerts means the real test normals differ from the calibration normals more than chance allows.");
table("visa-rules",["Scorer","Configuration","Defects caught","False alerts"],["descriptor","dinov2-engine","dinov2-native"].flatMap(s=>["as_shipped","full","full_pac"].map(r=>{const p=D.pooled[s][r];return[N[s],R[r],`${rate(p.det)} (${ci(p.det[2],p.det[3])})`,`${rate(p.fa)} (${ci(p.fa[2],p.fa[3])})`]})),[0,0,1,1]);
table("visa-auroc",["Scorer","Mean AUROC (95% interval)"],["descriptor","dinov2-engine","dinov2-native"].map(s=>[N[s],`${fmt(D.auroc[s][0],3)} (${fmt(D.auroc[s][1],3)} to ${fmt(D.auroc[s][2],3)})`]),[0,1]);
put("visa-auroc-sub","The probability that a defective image scores above a normal one, averaged over the classes with equal weight, from the full configuration's scores. It does not depend on any threshold.");
const M={"detection":"Defects caught","false_alert":"False alerts","auroc":"Mean AUROC"},W={"higher":"Higher, excludes zero","lower":"Lower, excludes zero","not resolved":"Not resolved, includes zero"};
const dv=(m,v)=>m==="auroc"?(v>0?"+":"")+fmt(v,3):(v>0?"+":"")+fmt(100*v,1)+" points";
table("visa-paired",["Comparison","Measure","Difference","95% interval","Reading"],D.paired.flatMap(p=>["detection","false_alert","auroc"].map(m=>[`${N[p.a]} minus ${N[p.b]}`,M[m],dv(m,p[m][0]),`${dv(m,p[m][1])} to ${dv(m,p[m][2])}`,W[p[m][3]]])),[0,0,1,1,0]);
put("visa-paired-sub","Each difference compares two scorers on exactly the same images, full configuration, marginal rule; the interval comes from a bootstrap that resamples images within each class and label.");
put("visa-paired-note","Many intervals are shown on this page and none is adjusted for multiple comparisons.");
const O={"caught":"Defect caught","missed":"Defect missed","false_alert":"False alert","passed":"Normal part passed"};
const box=document.getElementById("visa-examples");box.replaceChildren();
Object.keys(O).forEach(k=>{const e=D.examples[k],f=h("figure");if(e){const im=h("img");im.src="data:image/jpeg;base64,"+e[3];im.alt=`${O[k]}: ${e[0]}, as the engine sees it`;f.append(im)}else f.append(h("div",undefined,"empty"))
  const c=h("figcaption");c.append(h("strong",O[k]));c.append(e?`${e[0]} · score ${fmt(e[2],3)}`:"none");f.append(c);box.append(f)});
put("visa-examples-sub","Shipped descriptor, full configuration. For each outcome, the image whose score is the lower median of that outcome's images, shown as the engine sees it: 96 × 96 greyscale.");
const e=D.env;put("foot",`Generated from reports/visa/basira.json · protocol digest ${D.digest.slice(0,12)} · run of ${fmt(D.elapsed/60,0)} min: Python ${e.python} · NumPy ${e.numpy} · SciPy ${e.scipy} · Pillow ${e.pillow} · torch ${e.torch} · ${e.cpu}. Real public data, not MIZAN's own; no escape-rate certificate follows. Rebuild with python scripts/build_visa_report.py.`);
</script>
</body>
</html>
"""

CLASSES_PAGE = r"""<div class="kicker">Release 3.2 · real public images (VisA)</div>
<h1>BASIRA on VisA, per class</h1>
<p class="lede">Each class is one product line with its own reference bank and calibration set. Rates carry exact 95% intervals; AUROC carries a bootstrap interval. The summary is on <a href="index.html">BASIRA on real images</a>.</p>
<section id="visa-classes-section">
<div id="visa-classes"></div>
<div class="card"><h3>By VisA type, every configuration</h3><div class="scroll"><table id="visa-types"></table></div></div>
<div class="card"><h3>Repeated calibration, pooled over classes</h3><p class="sub" id="visa-study-sub"></p>
<div class="scroll"><table id="visa-study"></table></div><div id="visa-study-classes"></div><p class="note" id="visa-study-note"></p></div>
%%ATTRIBUTION%%
</section>
</main></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
%%HELPERS%%
%%COMMON%%
const SC=["descriptor","dinov2-engine","dinov2-native"],box=document.getElementById("visa-classes");box.replaceChildren();
const cell=(a,i)=>a[i+1]?`${rate([a[i],a[i+1]])} (${ci(a[i+2],a[i+3])})`:"–";
SC.forEach((s,i)=>{const card=h("div",undefined,"card");card.append(h("h3",N[s]+", full configuration"));const sc=h("div",undefined,"scroll"),t=h("table");t.id="visa-class-"+i;sc.append(t);card.append(sc);box.append(card);
  table(t.id,["Class","Type","Defects caught","False alerts","AUROC"],D.classes.map(([c,ty])=>{const a=D.per_class[s].full[c],u=D.auroc[s][c];return[c,ty,cell(a,4),cell(a,0),`${fmt(u[0],3)} (${fmt(u[1],3)} to ${fmt(u[2],3)})`]}),[0,0,1,1,1])});
table("visa-types",["Type","Scorer","Configuration","Defects caught","False alerts"],Object.keys(D.by_type.descriptor.full).flatMap(t=>SC.flatMap(s=>["as_shipped","full","full_pac"].map(r=>{const a=D.by_type[s][r][t];return[t,N[s],R[r],cell(a,4),cell(a,0)]}))),[0,0,0,1,1]);
const srow=(s,r)=>[N[s],String(r[0]),pct(r[1]),pct(r[2]),r[3]===null?"–":pct(r[3]),pct(r[4])];
table("visa-study",["Scorer","m","Mean false alerts",`Share of calibration sets above ${C.alpha}`,"Theoretical share","Mean detection"],SC.flatMap(s=>D.study[s].pooled.map(r=>srow(s,r))),[0,1,1,1,1,1]);
const sb=document.getElementById("visa-study-classes");sb.replaceChildren();
SC.forEach((s,i)=>{const d=h("details");d.append(h("summary",`${N[s]}: every class (indicative)`));const sc=h("div",undefined,"scroll"),t=h("table");t.id="visa-study-"+i;sc.append(t);d.append(sc);sb.append(d);
  table(t.id,["Class","m","Mean false alerts",`Share above ${C.alpha}`,"Theoretical share","Mean detection"],D.classes.flatMap(([c])=>(D.study[s].per_class[c]||[]).map(r=>[c,...srow(s,r).slice(1)])),[0,1,1,1,1,1])});
put("visa-study-sub",`With each class's full bank fixed, ${C.draws} calibration sets of m parts are drawn from that class's draw pool; each threshold's long-run false-alert rate is measured on held-out normal parts from the same class (the evaluation pool), and its detection on the test defects.`);
put("visa-study-note","Pooled rows average the classes with equal weight; per-class rows are indicative. No interval is reported for these shares. The theoretical share is what split-conformal calibration implies if the parts are exchangeable.");
</script>
</body>
</html>
"""


def build(source=ROOT / "reports" / "visa", out_dir=ROOT / "reports" / "visa"):
    record = json.loads((Path(source) / "basira.json").read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    pages = []
    for name, body, data, title, back, href in (
            ("index.html", SUMMARY_PAGE, summary(record), "MIZAN | BASIRA on real images", "Evaluation report", "../index.html"),
            ("classes.html", CLASSES_PAGE, classes_summary(record), "MIZAN | BASIRA on VisA, per class", "BASIRA on real images", "index.html")):
        body = body.replace("%%ATTRIBUTION%%", ATTRIBUTION_HTML).replace("%%COMMON%%", COMMON_JS)
        target = out_dir / name
        target.write_text(_page(body, data, title, back, href), encoding="utf-8")
        pages.append(target)
    return pages


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=ROOT / "reports" / "visa")
    parser.add_argument("--out-dir", default=ROOT / "reports" / "visa")
    args = parser.parse_args()
    for p in build(args.source, args.out_dir):
        print(p, p.stat().st_size)
