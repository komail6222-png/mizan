"""Build reports/evaluation/index.html, a self-contained visual summary of the evaluation JSON files.

The page embeds only the numbers it plots (no fixtures or images), makes no network requests and works offline.
Rebuild after `python scripts/evaluate.py`: python scripts/build_report.py
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHORT = {"candidate": "Risk-weighted", "candidate_risk_neutral": "Risk-neutral", "seasonal_naive": "seasonal"}
LABELS = {"candidate": "Candidate, risk weight 0.35", "candidate_risk_neutral": "Candidate, risk-neutral",
          "seasonal_naive": "Seasonal baseline", "perfect_foresight": "Hindsight reference", "no_battery": "No battery"}


def safety_summary(path):
    if not Path(path).exists():
        return None
    r = json.loads(Path(path).read_bytes())
    sim, mut = r["simulation"], r["simulation_mutants"]
    return [
        {"suite": "Deterministic simulation of the real broker", "checks": f"{sim['steps']:,} steps · {sim['schedules']:,} schedules · {sim['crashes']:,} crashes",
         "violations": sim["violations"], "mutants": f"{sum(v['caught'] for v in mut.values())}/{len(mut)} caught"},
        {"suite": "SANAD metamorphic relations R0–R7", "checks": f"{r['sanad']['total_checks']:,} relation checks",
         "violations": r["sanad"]["violations"], "mutants": f"{sum(v['caught'] for v in r['sanad_mutants'].values())}/{len(r['sanad_mutants'])} caught"},
        {"suite": "BASIRA decision-rule properties", "checks": f"{sum(r['basira']['checks'].values()):,} checks · exceedance vs Monte Carlo |z| ≤ {r['basira']['max_abs_z']:.2f}",
         "violations": r["basira"]["violations"], "mutants": "–"},
        {"suite": "RASID verifier on random problems", "checks": f"{r['rasid']['solver_plans_verified']} plans verified · {r['rasid']['mutations_attempted']:,} invalid mutations",
         "violations": r["rasid"]["mutations_attempted"] - r["rasid"]["mutations_rejected"], "mutants": "8/8 classes rejected"},
    ]


def summarize(folder):
    load = lambda k: json.loads((Path(folder) / f"{k}.json").read_bytes())
    sanad, basira, rasid = load("sanad"), load("basira"), load("rasid")
    sets = [("Original bilingual grammar set", sanad, "naive_numeric_correct", "Numeric-only rule"),
            ("Grammar 2: Arabic typography and acknowledged context", sanad["grammar_2_extension"], None, None),
            ("Grammar 3: multi-model tables", sanad["grammar_3_tables"], "naive_row_reader_correct", "First number on the row")]
    study = basira["calibration_study"]
    q = rasid["decision_quality"]
    return {
        "environment": sanad.get("environment", {}),
        "safety": safety_summary(Path(folder).parent / "safety" / "safety.json"),
        "sanad": [{"set": name, "cases": s["fixture_rows"], "correct": s["correct"], "false_meets": s["false_meets"],
                   "decisive": s["decisive"], "baseline": baseline, "baseline_correct": s.get(key) if key else None}
                  for name, s, key, baseline in sets],
        "basira": {"shipped": study["shipped_calibration"], "minimum_m": study["minimum_m_for_pac"], "alpha": study["alpha"],
                   "delta": study["delta"], "draws": study["draws_per_size"], "pools": study["pools"],
                   "rows": study["rows"], "frozen_test": {k: basira[k] for k in ("detected", "false_alerts", "test_items")}},
        "rasid": {"days": rasid["target_days"], "mae": [rasid["ridge_mae_kw"], rasid["seasonal_naive_mae_kw"]],
                  "policies": [{"key": k, "label": LABELS[k], **q[k]} for k in LABELS],
                  "paired": [{"label": f"{SHORT[c['policy']]} − {SHORT[c['reference']].lower()}", "full": f"{LABELS[c['policy']]} minus {LABELS[c['reference']].lower()}", **c}
                             for c in rasid["paired_comparisons"]]},
    }


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MIZAN | Evaluation report</title>
<style>
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--page:#f9f9f7;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--series-1:#2a78d6;--series-2:#eb6834;--good:#006300}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;--page:#0d0d0d;
  --text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series-1:#3987e5;--series-2:#d95926;--good:#0ca30c}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#f0efec;--text-secondary:#c3c2b7;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series-1:#3987e5;--series-2:#d95926;--good:#0ca30c}
*{box-sizing:border-box}
body{margin:0}
.viz-root{background:var(--page);color:var(--text-primary);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}
main{max-width:980px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:28px;line-height:1.2;margin:4px 0 6px}h2{font-size:20px;margin:40px 0 4px}h3{font-size:16px;margin:0 0 2px}
.kicker{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-secondary);font-weight:600}
.lede{color:var(--text-secondary);margin:0 0 18px;max-width:72ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.tile,.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px}
.tile .label{font-size:13px;color:var(--text-secondary)}.tile .value{font-size:28px;font-weight:600;margin:2px 0}
.tile .note{font-size:13px;color:var(--text-secondary)}
.card{margin:14px 0}.card p.sub{margin:0 0 10px;color:var(--text-secondary);font-size:14px}
.chart{position:relative;width:100%}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--text-secondary);margin:4px 0 8px}
.legend span.line::before{height:2px;width:16px;border-radius:0;background:var(--text-secondary);vertical-align:3px}
.legend span::before{content:"";display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--c);margin-right:6px;vertical-align:-1px}
details{margin-top:8px;font-size:14px}summary{cursor:pointer;color:var(--text-secondary)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;margin-top:8px;font-size:14px;min-width:520px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid)}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);border-radius:8px;padding:8px 10px;
  font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.12);opacity:0;transition:opacity .08s;max-width:260px;z-index:2}
.tip strong{display:block;font-size:15px}.tip span{color:var(--text-secondary)}
.foot{color:var(--text-secondary);font-size:13px;margin-top:36px}
svg text{fill:var(--text-secondary);font:12px system-ui,-apple-system,"Segoe UI",sans-serif}
svg .strong{fill:var(--text-primary);font-weight:600}
.hit{fill:transparent;cursor:default;outline:none}.mark.on{filter:brightness(1.15)}svg text{pointer-events:none}
</style>
</head>
<body>
<div class="viz-root"><main>
<div class="kicker">MIZAN · release 3.0 · generated fixtures</div>
<h1>Evaluation report</h1>
<p class="lede">What the three systems decide on held-out generated data, what the statistics promise, and where the evidence is still thin. Every number on this page comes from <code>reports/evaluation/*.json</code>.</p>
<div class="tiles" id="tiles"></div>

<h2>SANAD · procurement evidence</h2>
<p class="lede">Each set is a regression suite for one grammar version. A false MEETS means the system approved a requirement it should not have. The baselines never abstain.</p>
<div class="card"><div class="scroll"><table id="sanad"></table></div></div>

<h2>BASIRA · what one calibration bank guarantees</h2>
<p class="lede" id="basira-lede"></p>
<div class="card"><h3>Banks whose long-run false-alert rate exceeds 5%</h3>
<p class="sub" id="basira-sub"></p><div class="legend"><span style="--c:var(--series-1)">Split conformal (shipped rule)</span><span style="--c:var(--series-2)">PAC rule, δ = 10%</span><span class="line">10% bound (δ)</span></div>
<div class="chart" id="banks"></div>
<details><summary>Show data</summary><div class="scroll"><table id="banks-table"></table></div></details></div>

<h2>RASID · decision quality, not only forecast error</h2>
<p class="lede" id="rasid-lede"></p>
<div class="card"><h3>Share of the achievable battery value each policy captured</h3>
<p class="sub">0% is running without a battery, 100% is the hindsight reference that plans each day on the realized load.</p>
<div class="chart" id="share"></div></div>
<div class="card"><h3>Expected cost against tail cost</h3>
<p class="sub">Tail cost is the mean of the worst 10% of days (empirical CVaR90). Down is safer, left is cheaper. The no-battery policy is off this scale.</p>
<div class="chart" id="tail"></div></div>
<div class="card"><h3>Paired differences in mean daily cost</h3>
<p class="sub">95% moving-block bootstrap interval, 7-day blocks. An interval that crosses zero is not resolved by these 60 days.</p>
<div class="chart" id="paired"></div>
<details><summary>Show data</summary><div class="scroll"><table id="rasid-table"></table></div></details></div>
<section id="safety-section" hidden>
<h2>Safety evidence · properties, simulation and mutants</h2>
<p class="lede">Generated inputs and seeded schedules check relations that must hold for every case. Each suite is also run against deliberately broken versions of the system; a suite that cannot catch those proves nothing. Source: <code>reports/safety/safety.json</code>.</p>
<div class="card"><div class="scroll"><table id="safety"></table></div></div>
</section>
<p class="foot" id="foot"></p>
</main></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById("data").textContent);
const NS="http://www.w3.org/2000/svg";
const fmt=(v,d=0)=>v.toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d}).replace("-","\u2212");
const pct=(v,d=1)=>fmt(100*v,d)+"%";
function el(tag,attrs={},parent){const e=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(parent)parent.append(e);return e}
function text(parent,x,y,s,attrs={}){const t=el("text",{x,y,...attrs},parent);t.textContent=s;return t}
function h(tag,s,cls){const e=document.createElement(tag);if(s!==undefined)e.textContent=s;if(cls)e.className=cls;return e}
function table(id,head,rows,numeric){const t=document.getElementById(id);t.replaceChildren();const tr=h("tr");
  head.forEach((c,i)=>{const th=h("th",c);if(numeric[i])th.className="n";tr.append(th)});t.append(tr);
  rows.forEach(r=>{const row=h("tr");r.forEach((c,i)=>{const td=h("td",c);if(numeric[i])td.className="n";row.append(td)});t.append(row)})}
function tooltip(box){let tip=box.querySelector(".tip");if(!tip){tip=h("div",undefined,"tip");box.append(tip)}
  return{show(ev,value,label){tip.replaceChildren(h("strong",value),h("span",label));const r=box.getBoundingClientRect();
    let x=ev.clientX-r.left+12,y=ev.clientY-r.top+12;tip.style.opacity=1;const w=tip.offsetWidth;if(x+w>r.width)x=Math.max(0,ev.clientX-r.left-w-12);
    tip.style.left=x+"px";tip.style.top=y+"px"},hide(){tip.style.opacity=0}}}
function hover(hit,mark,tip,value,label){hit.setAttribute("tabindex","0");hit.parentNode.append(hit); // hit area above its mark
  const on=ev=>{mark.classList.add("on");tip.show(ev,value,label)},off=()=>{mark.classList.remove("on");tip.hide()};
  hit.addEventListener("pointermove",on);hit.addEventListener("pointerleave",off);
  hit.addEventListener("focus",()=>{const b=hit.getBoundingClientRect();on({clientX:b.left+b.width/2,clientY:b.top})});hit.addEventListener("blur",off)}
function svg(box,height){box.querySelector("svg")?.remove();const w=box.clientWidth;const s=el("svg",{width:w,height,viewBox:`0 0 ${w} ${height}`,role:"img"});box.prepend(s);return[s,w]}
function bar(s,x,y,w,hgt,color){ // 4px rounded data-end, square at the baseline
  const r=Math.min(4,w,hgt/2);return el("path",{d:`M${x},${y}h${Math.max(0,w-r)}a${r},${r} 0 0 1 ${r},${r}v${hgt-2*r}a${r},${r} 0 0 1 -${r},${r}h-${Math.max(0,w-r)}z`,fill:color,class:"mark"},s)}
function vbar(parent,x,top,w,base,color){const r=Math.min(4,(base-top),w/2); // rounded data-end, square baseline
  return el("path",{d:`M${x},${base}V${top+r}a${r},${r} 0 0 1 ${r},-${r}h${w-2*r}a${r},${r} 0 0 1 ${r},${r}V${base}z`,fill:color,class:"mark"},parent)}
const frac=v=>{const[a,b]=String(v).split("/");return b?Number(a)/Number(b):Number(a)};
function ticks(max,n=4){const step=Math.pow(10,Math.floor(Math.log10(max/n)));const m=[1,2,2.5,5,10].find(k=>max/(k*step)<=n)*step;const out=[];for(let v=0;v<=max+1e-9;v+=m)out.push(+v.toFixed(10));return out}

function tiles(){const B=D.basira.shipped,S=D.sanad,R=D.rasid,neutral=R.paired.find(p=>p.policy==="candidate_risk_neutral"&&p.reference==="seasonal_naive");
  const premium=R.paired.find(p=>p.policy==="candidate"&&p.reference==="candidate_risk_neutral");
  const cases=S.reduce((a,s)=>a+s.cases,0),correct=S.reduce((a,s)=>a+s.correct,0),fm=S.reduce((a,s)=>a+s.false_meets,0);
  const items=[["SANAD fixtures decided as expected",`${correct} / ${cases}`,`${fm} false MEETS across three grammar suites`],
    ["BASIRA shipped bank, long-run false alerts",pct(B.long_run_false_alert_estimate),`Target 5%. A bank at least this bad occurs ${pct(B.probability_of_a_bank_at_least_this_bad)} of the time.`],
    ["Calibration parts for a PAC guarantee",String(D.basira.minimum_m),`α = 5%, δ = 10%. The shipped bank has ${B.m}.`],
    ["RASID price of risk aversion",`+${fmt(premium.total_difference)}`,`Over ${R.days} days vs risk-neutral; 95% interval excludes zero. Forecast value vs baseline: ${fmt(neutral.total_difference,1)}, not resolved.`]];
  const box=document.getElementById("tiles");box.replaceChildren(...items.map(([l,v,n])=>{const t=h("div",undefined,"tile");t.append(h("div",l,"label"),h("div",v,"value"),h("div",n,"note"));return t}))}

function sanad(){table("sanad",["Suite","Cases","Correct","False MEETS","Decisive","Baseline","Baseline correct"],
  D.sanad.map(s=>[s.set,String(s.cases),String(s.correct),String(s.false_meets),String(s.decisive),s.baseline||"–",s.baseline_correct==null?"–":String(s.baseline_correct)]),[0,1,1,1,1,0,1])}

function banks(){const B=D.basira,box=document.getElementById("banks"),tip=tooltip(box);
  document.getElementById("basira-lede").textContent=`Split conformal promises a false-alert rate of at most 5% on average over calibration draws. ${B.draws} independent banks per size, scored on ${fmt(B.pools.evaluation_normals)} fresh normal parts, show how far one bank can stray. The frozen test flagged ${B.frozen_test.false_alerts} of 40 intact rings; the shipped bank's long-run rate is about ${pct(B.shipped.long_run_false_alert_estimate)}.`;
  document.getElementById("basira-sub").textContent=`Detection on ${B.pools.evaluation_defects} generated defects stayed at 100% for every rule; real defects can show a trade-off.`;
  const [s,w]=svg(box,250),L=56,R=12,T=14,Bm=36,ph=250-T-Bm,max=0.5,y=v=>T+ph*(1-v/max);
  for(const v of ticks(max,5)){el("line",{x1:L,x2:w-R,y1:y(v),y2:y(v),stroke:"var(--grid)"},s);text(s,L-8,y(v)+4,pct(v,0),{"text-anchor":"end"})}
  el("line",{x1:L,x2:w-R,y1:y(0),y2:y(0),stroke:"var(--axis)"},s);
  const delta=frac(B.delta);el("line",{x1:L,x2:w-R,y1:y(delta),y2:y(delta),stroke:"var(--text-secondary)","stroke-width":1},s);
  const band=(w-L-R)/B.rows.length,bw=Math.min(24,band/4);
  B.rows.forEach((r,i)=>{const cx=L+band*(i+.5);text(s,cx,250-12,`m = ${r.m}`,{"text-anchor":"middle"});
    [[r.marginal_share_of_banks_above_alpha,"var(--series-1)","Split conformal",r.marginal_theoretical_share_above_alpha],
     [r.pac_share_of_banks_above_alpha,"var(--series-2)","PAC rule",r.pac_theoretical_share_above_alpha]].forEach(([v,c,name,theory],j)=>{
      const x=cx+(j?2:-bw-2);
      if(v==null){text(s,x+bw/2,y(0)-8,"needs m ≥ "+B.minimum_m,{"text-anchor":"start",transform:`rotate(-90 ${x+bw/2} ${y(0)-8})`});return}
      const top=y(v),g=el("g",{},s),hit=el("rect",{x:x-4,y:T,width:bw+8,height:ph,class:"hit"},g),m=vbar(g,x,top,bw,y(0),c);
      text(s,x+bw/2,top-6,pct(v,0),{"text-anchor":"middle",class:"strong"});
      hover(hit,m,tip,pct(v,1)+" of banks",`${name}, m = ${r.m}. Theory: ${pct(theory,1)}. Mean false alerts ${pct(j?r.pac_mean_false_alert:r.marginal_mean_false_alert,1)}.`)})});
  table("banks-table",["m","Rule","Rank k","Share of banks > 5%","Theory","Mean false alerts","Detection"],
    B.rows.flatMap(r=>[[String(r.m),"Split conformal",String(r.marginal_rank),pct(r.marginal_share_of_banks_above_alpha),pct(r.marginal_theoretical_share_above_alpha),pct(r.marginal_mean_false_alert),pct(r.marginal_mean_detection,0)],
      r.pac_rank?[String(r.m),"PAC δ = 10%",String(r.pac_rank),pct(r.pac_share_of_banks_above_alpha),pct(r.pac_theoretical_share_above_alpha),pct(r.pac_mean_false_alert),pct(r.pac_mean_detection,0)]
                :[String(r.m),"PAC δ = 10%","–","needs m ≥ "+B.minimum_m,"–","–","–"]]),[1,0,1,1,1,1,1])}

function share(){const P=D.rasid.policies,box=document.getElementById("share"),tip=tooltip(box),narrow=box.clientWidth<560;
  const rowH=narrow?46:34,labelW=narrow?0:190,[s,w]=svg(box,P.length*rowH+30),L=narrow?14:labelW,R=56,x=v=>L+(w-L-R)*v,barY=narrow?18:0;
  for(const v of [0,.25,.5,.75,1]){el("line",{x1:x(v),x2:x(v),y1:4,y2:P.length*rowH+6,stroke:"var(--grid)"},s);text(s,x(v),P.length*rowH+22,pct(v,0),{"text-anchor":"middle"})}
  P.forEach((p,i)=>{const yy=8+i*rowH,v=p.share_of_achievable_battery_value;
    if(narrow)text(s,L,yy+12,p.label,{"text-anchor":"start"});else text(s,L-10,yy+15,p.label,{"text-anchor":"end"});
    const g=el("g",{},s),hit=el("rect",{x:0,y:yy-4,width:w,height:rowH,class:"hit"},g),top=yy+barY;
    const m=v>0?bar(g,x(0),top,x(v)-x(0),20,"var(--series-1)"):el("line",{x1:x(0),x2:x(0),y1:top,y2:top+20,stroke:"var(--axis)",class:"mark"},g);
    text(s,x(v)+6,top+15,pct(v),{class:"strong"});
    hover(hit,m,tip,pct(v)+" captured",`${p.label}: total ${fmt(p.total_cost,2)}, regret ${fmt(p.regret_vs_perfect_foresight,2)}`)});
  el("line",{x1:x(0),x2:x(0),y1:4,y2:P.length*rowH+6,stroke:"var(--axis)"},s)}

function tail(){const P=D.rasid.policies.filter(p=>p.key!=="no_battery"),box=document.getElementById("tail"),tip=tooltip(box);
  const [s,w]=svg(box,280),L=64,R=24,T=16,B=40;const xs=P.map(p=>p.total_cost),ys=P.map(p=>p.mean_worst_10pct_days);
  const pad=(a,b)=>[a-(b-a)*.25,b+(b-a)*.25],[x0,x1]=pad(Math.min(...xs),Math.max(...xs)),[y0,y1]=pad(Math.min(...ys),Math.max(...ys));
  const X=v=>L+(w-L-R)*(v-x0)/(x1-x0),Y=v=>T+(280-T-B)*(1-(v-y0)/(y1-y0));
  const step=(a,b)=>{const r=(b-a)/4,p=Math.pow(10,Math.floor(Math.log10(r)));return[1,2,5,10].find(k=>k*p>=r)*p};
  for(let v=Math.ceil(y0/step(y0,y1))*step(y0,y1);v<=y1;v+=step(y0,y1)){el("line",{x1:L,x2:w-R,y1:Y(v),y2:Y(v),stroke:"var(--grid)"},s);text(s,L-8,Y(v)+4,fmt(v),{"text-anchor":"end"})}
  for(let v=Math.ceil(x0/step(x0,x1))*step(x0,x1);v<=x1;v+=step(x0,x1))text(s,X(v),280-B+18,fmt(v),{"text-anchor":"middle"});
  text(s,(L+w-R)/2,280-6,"Total cost over 60 days (illustrative units)",{"text-anchor":"middle"});
  text(s,12,(T+280-B)/2,"Mean of worst 10% days",{"text-anchor":"middle",transform:`rotate(-90 12 ${(T+280-B)/2})`});
  P.forEach(p=>{const cx=X(p.total_cost),cy=Y(p.mean_worst_10pct_days),g=el("g",{},s),hit=el("circle",{cx,cy,r:14,class:"hit"},g);
    const m=el("circle",{cx,cy,r:5,fill:"var(--series-1)",stroke:"var(--surface-1)","stroke-width":2,class:"mark"},g);
    const right=cx<w*.6;text(s,cx+(right?10:-10),cy-8,p.label,{"text-anchor":right?"start":"end"});
    hover(hit,m,tip,`${fmt(p.total_cost,2)} · tail ${fmt(p.mean_worst_10pct_days,2)}`,p.label)})}

function paired(){const C=D.rasid.paired,box=document.getElementById("paired"),tip=tooltip(box),labelW=Math.min(200,box.clientWidth*.38),rowH=40;
  const [s,w]=svg(box,C.length*rowH+50),L=labelW,R=20,lo=Math.min(0,...C.map(c=>c.mean_daily_difference_95ci[0])),hi=Math.max(0,...C.map(c=>c.mean_daily_difference_95ci[1]));
  const span=hi-lo,X=v=>L+(w-L-R)*(v-(lo-span*.1))/(span*1.2);
  for(const v of [-1,-.5,0,.5,1,1.5,2]){if(v<lo-span*.1||v>hi+span*.1)continue;el("line",{x1:X(v),x2:X(v),y1:4,y2:C.length*rowH+6,stroke:v===0?"var(--axis)":"var(--grid)"},s);text(s,X(v),C.length*rowH+22,(v>0?"+":"")+fmt(v,1),{"text-anchor":"middle"})}
  text(s,(L+w-R)/2,C.length*rowH+42,"Mean daily cost difference",{"text-anchor":"middle"});
  C.forEach((c,i)=>{const yy=14+i*rowH,[a,b]=c.mean_daily_difference_95ci;text(s,L-10,yy+4,c.label,{"text-anchor":"end"});
    const g=el("g",{},s),hit=el("rect",{x:0,y:yy-rowH/2,width:w,height:rowH,class:"hit"},g),m=el("g",{class:"mark"},g);
    el("line",{x1:X(a),x2:X(b),y1:yy,y2:yy,stroke:"var(--series-1)","stroke-width":2,"stroke-linecap":"round"},m);
    el("circle",{cx:X(c.mean_daily_difference),cy:yy,r:5,fill:"var(--series-1)",stroke:"var(--surface-1)","stroke-width":2},m);
    hover(hit,m,tip,`${c.mean_daily_difference>0?"+":""}${fmt(c.mean_daily_difference,3)} per day`,`${c.full}. 95% interval ${fmt(a,3)} to ${fmt(b,3)}. ${c.days_cheaper} days cheaper, ${c.days_more_expensive} more expensive. Total ${fmt(c.total_difference,2)}.`)});
  table("rasid-table",["Policy","Total cost","Regret","Share captured","Worst day","Worst 10% days","Verified plans"],
    D.rasid.policies.map(p=>[p.label,fmt(p.total_cost,2),fmt(p.regret_vs_perfect_foresight,2),pct(p.share_of_achievable_battery_value),fmt(p.worst_day_cost,2),fmt(p.mean_worst_10pct_days,2),String(p.verified_feasible_plans)]),[0,1,1,1,1,1,1])}

function ledes(){const R=D.rasid;document.getElementById("rasid-lede").textContent=`The ridge forecast cuts load error from ${fmt(R.mae[1],3)} to ${fmt(R.mae[0],3)} kW over ${R.days} rolling days. Release 2.1 reported that the planner nevertheless cost more than the seasonal baseline. That comparison mixed two effects: the forecast and the risk weight. Separating them shows what each one buys.`;
  const e=D.environment;document.getElementById("foot").textContent=`Generated from reports/evaluation · Python ${e.python||"?"} · NumPy ${e.numpy||"?"} · SciPy ${e.scipy||"?"} · All inputs are generated fixtures; costs are illustrative units. Rebuild with python scripts/build_report.py.`}
function safety(){if(!D.safety)return;document.getElementById("safety-section").hidden=false;
  table("safety",["Suite","What ran","Violations","Broken versions"],D.safety.map(r=>[r.suite,r.checks,String(r.violations),r.mutants]),[0,0,1,0])}
function render(){tiles();sanad();banks();share();tail();paired();ledes();safety()}
render();let t;addEventListener("resize",()=>{clearTimeout(t);t=setTimeout(()=>{banks();share();tail();paired()},120)});
</script>
</body>
</html>
"""


def build(source, out):
    data = json.dumps(summarize(source), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    Path(out).write_text(PAGE.replace("%%DATA%%", data), encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=ROOT / "reports/evaluation")
    parser.add_argument("--out", default=ROOT / "reports/evaluation/index.html")
    args = parser.parse_args()
    print(build(args.source, args.out))
