"""Build the evaluation report: reports/evaluation/index.html, a self-contained visual summary of the evaluation JSON
files, and reports/evaluation/models.html, the comparison of the pinned Hugging Face models with the current components
(from reports/hf/, when that folder exists beside the source folder).

The pages embed only the numbers they show (no fixtures or images), make no network requests and work offline.
Rebuild after `python scripts/evaluate.py` or `python scripts/derive_hf_intervals.py`: python scripts/build_report.py
"""
import argparse
import base64
import hashlib
import json
import re
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


HF_POLICY = {"candidate": "ridge risk-weighted", "candidate_risk_neutral": "ridge risk-neutral", "seasonal_naive": "seasonal baseline",
             "chronos": "Chronos risk-weighted", "chronos_risk_neutral": "Chronos risk-neutral"}
SANAD_METRICS = ("recall_at_1", "recall_at_3", "mrr_at_4")  # embedded as arrays in this order
SANAD_BY_LANGUAGE = (("en", "en"), ("en", "ar"), ("ar", "ar"), ("ar", "en"))  # (query label, datasheet language), embedded in this order
ACCURACY = ("load_mae", "mean_pinball", "truncated_crps", "coverage_80_load")  # chronos_minus_ridge_* in reports/hf/rasid_intervals.json


def fail(message):
    raise SystemExit("build_report: " + message)


def derived_intervals(folder, *pairs):
    """reports/hf/rasid_intervals.json, refused unless it was derived from each (published path, local file) given."""
    intervals = json.loads((Path(folder) / "rasid_intervals.json").read_bytes())
    for published, local in pairs:
        if intervals["source"]["files"].get(published) != hashlib.sha256(Path(local).read_bytes()).hexdigest():
            fail(f"rasid_intervals.json was not derived from this {published}; run python scripts/derive_hf_intervals.py")
    return intervals, {c["comparison"]: c for c in intervals["comparisons"]}


def hf_summary(folder):
    """The figures the models page shows, read from reports/hf/: never the per-item, per-day or gate records."""
    load = lambda k: json.loads((Path(folder) / f"{k}.json").read_bytes())
    sanad, basira, rasid = load("sanad"), load("basira"), load("rasid")
    intervals, derived = derived_intervals(folder, ("reports/hf/rasid.json", Path(folder) / "rasid.json"))
    run = lambda r: {"repo": r["identity"]["repo"], "revision": r["identity"]["revision"][:12], "gate": r["gate"]["passed"],
                     "identical": r["repeat_run"]["identical"]}

    def vision(side):  # short keys for the page budget: rate, p_worse and the split-conformal false_alert, share_above (banks above alpha)
        study, test, shipped = side["calibration_study"], side["frozen_test"], side["calibration_study"]["shipped_calibration"]
        intact = sum(item["condition"] == "intact" for item in test["rows"])
        return {"frozen_test": {"detected": test["detected"], "false_alerts": test["false_alerts"], "intact": intact, "defects": test["test_items"] - intact},
                "shipped": {"m": shipped["m"], "rate": shipped["long_run_false_alert_estimate"], "p_worse": shipped["probability_of_a_bank_at_least_this_bad"]},
                "study": [{"m": row["m"], "false_alert": row["marginal_mean_false_alert"], "share_above": row["marginal_share_of_banks_above_alpha"]}
                          for row in study["rows"]],
                "detection": min(row["marginal_mean_detection"] for row in study["rows"])}

    fb, fm = rasid["baseline"]["forecast"], rasid["model"]["forecast"]
    qb, qm = rasid["baseline"]["decision_quality"], rasid["model"]["decision_quality"]
    pairs = rasid["model"]["paired_comparisons"]
    ranks = lambda side: {kind: [side["metrics"][kind]["all"][m] for m in SANAD_METRICS] for kind in ("bilingual", "en", "ar")}
    interval = lambda name: {"d": derived[name]["mean_difference"], "ci": derived[name]["interval_95"]}
    planner = intervals["risk_weighted_planner"]
    # The study's own precision for each long-run rate ("about ±0.35 percentage points"); a missing statement stops the build.
    precision = float(re.search(r"±([0-9.]+) percentage points", basira["baseline"]["calibration_study"]["scope"]).group(1))
    return {
        "fixture_only": all(r["fixture_only"] for r in (sanad, basira, rasid)),
        "environment": {k: rasid["environment"][k] for k in ("python", "numpy", "scipy", "torch")},
        "basira": {**run(basira), "alpha": basira["settings"]["alpha"], "draws": basira["settings"]["draws"], "precision_points": precision,
                   "baseline": vision(basira["baseline"]), "model": vision(basira["model"])},
        "rasid": {**run(rasid), "days": rasid["model"]["target_days"], "levels": fm["shared_levels"], "pv_clipped": fm["pv_values_clipped"],
                  "chronos_levels": rasid["settings"]["chronos_levels"], "pairing": rasid["settings"]["pairing"],
                  "forecast": {"mae": [fb["ridge_mae_kw"], fm["chronos_mae_kw"]], "pinball": [fb["ridge_mean_pinball"], fm["chronos_mean_pinball"]],
                               "crps": [fb["ridge_truncated_crps"], fm["chronos_truncated_crps"]],
                               "coverage": [fb["ridge_coverage_80_load"], fm["chronos_coverage_80_load"]]},
                  # Chronos minus ridge per day, with the paired 95% interval derived from the same per-origin values
                  "accuracy": [{"k": k, **interval("chronos_minus_ridge_" + k)} for k in ACCURACY],
                  "cost": {"neutral": [qb["candidate_risk_neutral"]["total_cost"], qm["chronos_risk_neutral"]["total_cost"]],
                           "weighted": [qb["candidate"]["total_cost"], qm["chronos"]["total_cost"]]},
                  "tail": {"cvar": [qb["candidate"]["mean_worst_10pct_days"], qm["chronos"]["mean_worst_10pct_days"]],
                           "worst": [qb["candidate"]["worst_day_cost"], qm["chronos"]["worst_day_cost"]]},
                  "premium": {"ridge": interval("candidate_minus_candidate_risk_neutral_cost"), "chronos": interval("chronos_minus_chronos_risk_neutral_cost")},
                  "planner": {"risk": planner["risk_weight"], "beta": planner["cvar_beta"], "weights": planner["chronos_scenario_weights"],
                              "costliest": planner["chronos_cvar_is_costliest_scenario"]},
                  "verified": min(q["verified_feasible_plans"] for q in [*qb.values(), *qm.values()]), "bootstrap": pairs[0]["bootstrap"],
                  "shipped_gap": max(rasid["gate"]["total_cost_relative_difference"][k] for k in ("candidate", "candidate_risk_neutral")),
                  "tolerance": rasid["settings"]["gate_tolerances"]["candidate_totals_relative"],
                  "paired": [{"label": f"{HF_POLICY[c['policy']]} − {HF_POLICY[c['reference']]}",
                              **{k: c[k] for k in ("policy", "reference", "mean_daily_difference", "mean_daily_difference_95ci", "days_cheaper", "days_more_expensive")}}
                             for c in pairs]},
        "sanad": {**run(sanad), "queries": sanad["model"]["queries"], "unit": sanad["model"]["queries_unit"], "documents": sanad["model"]["documents"],
                  "by_language": sanad["model"]["queries_by_language"],
                  "lexical": ranks(sanad["baseline"]), "e5": ranks(sanad["model"]), "notes": sanad["notes"],
                  # recall@1 by (query label, datasheet language)
                  "lang": {name: [sanad[side]["metrics"][q][d]["recall_at_1"] for q, d in SANAD_BY_LANGUAGE] for name, side in (("lexical", "baseline"), ("e5", "model"))}},
    }


def hf_folder(folder, hf_source=None):
    """The Hugging Face evidence: by default the source folder's sibling hf/, like safety/; None when it is absent."""
    hf = Path(hf_source) if hf_source else Path(folder).parent / "hf"
    return hf if hf.is_dir() else None


def summarize(folder):
    load = lambda k: json.loads((Path(folder) / f"{k}.json").read_bytes())
    sanad, basira, rasid = load("sanad"), load("basira"), load("rasid")
    sets = [("Original bilingual grammar set", sanad, "naive_numeric_correct", "Numeric-only rule"),
            ("Grammar 2: Arabic typography and acknowledged context", sanad["grammar_2_extension"], None, None),
            ("Grammar 3: multi-model tables", sanad["grammar_3_tables"], "naive_row_reader_correct", "First number on the row")]
    study = basira["calibration_study"]
    q = rasid["decision_quality"]
    summary = {
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
    hf = hf_folder(folder)
    if hf and (hf / "rasid_intervals.json").exists():  # the paired interval of the ridge and seasonal-naive errors
        _, derived = derived_intervals(hf, ("reports/evaluation/rasid.json", Path(folder) / "rasid.json"))
        c = derived["ridge_minus_seasonal_naive_load_mae"]
        summary["rasid"]["mae_gap"] = {"d": c["mean_difference"], "ci": c["interval_95"]}
    return summary


def models_summary(folder, hf_source=None):
    """The models page's data, or None when there is no Hugging Face evidence."""
    hf = hf_folder(folder, hf_source)
    return {"hf": hf_summary(hf)} if hf else None


# Readex Pro: Copyright 2018 The Readex Pro Project Authors (https://github.com/ThomasJockin/readexpro), with Reserved Font
# Name "RevReading Lexend". Licensed under the SIL Open Font License, Version 1.1 (mizan/web/fonts/OFL-ReadexPro.txt).
STYLE = r"""<style>
/* Readex Pro (Latin subset): Copyright 2018 The Readex Pro Project Authors (https://github.com/ThomasJockin/readexpro), with Reserved Font Name "RevReading Lexend". Licensed under the SIL Open Font License, Version 1.1 (OFL-1.1); full text in OFL-ReadexPro.txt. */
@font-face{font-family:"Readex";src:url(data:font/woff2;base64,%%FONT_WOFF2%%) format("woff2");font-weight:160 700;font-display:swap}
.viz-root{color-scheme:light;--surface-1:#f5f7f8;--page:#e6eaee;--text-primary:#0e1c36;--text-secondary:#4b586f;--muted:#6b7790;
  --grid:#dbe1e7;--axis:#c8d0d9;--border:rgba(14,28,54,.13);--series-1:#0e1c36;--series-2:#9c6b1f;--good:#256b5b;--accent:#855a17;--focus:#8a5f1c}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#121e33;--page:#0b1424;
  --text-primary:#eef2f7;--text-secondary:#aab5c6;--grid:#1f2c42;--axis:#31415c;--border:rgba(255,255,255,.10);--series-1:#9fb8e8;--series-2:#ddb56b;--good:#6fc2ad;--accent:#ddb56b;--focus:#ddb56b}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#121e33;--page:#0b1424;--text-primary:#eef2f7;--text-secondary:#aab5c6;
  --grid:#1f2c42;--axis:#31415c;--border:rgba(255,255,255,.10);--series-1:#9fb8e8;--series-2:#ddb56b;--good:#6fc2ad;--accent:#ddb56b;--focus:#ddb56b}
*{box-sizing:border-box}
body{margin:0}
.viz-root{background:var(--page);color:var(--text-primary);font:350 15.5px/1.55 "Readex",system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased}
.bar{border-bottom:1px solid var(--axis)}.bar div{max-width:980px;margin:0 auto;padding:0 20px;min-height:64px;display:flex;align-items:center;justify-content:space-between;gap:16px}
.brand{font-weight:640;letter-spacing:.06em;font-size:19px;text-decoration:none;color:inherit}
.bar a.back{font-size:14px;font-weight:500;color:inherit;text-decoration:none;border-bottom:1px solid var(--axis)}
main{max-width:980px;margin:0 auto;padding:34px 20px 60px}
h1{font-size:clamp(30px,4.6vw,46px);line-height:1.05;letter-spacing:-.025em;font-weight:600;margin:6px 0 10px}h2{font-size:22px;font-weight:600;letter-spacing:-.01em;margin:48px 0 4px}h3{font-size:16px;font-weight:600;margin:0 0 2px}
.kicker{font-size:14.5px;color:var(--accent);font-weight:550}
.lede{color:var(--text-secondary);margin:0 0 18px;max-width:72ch}.lede a{color:var(--text-primary)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.tile,.card{background:var(--surface-1);border:1px solid var(--border);border-radius:14px;padding:18px}
.tile .label{font-size:13px;color:var(--text-secondary)}.tile .value{font-size:30px;font-weight:560;letter-spacing:-.02em;margin:2px 0;font-variant-numeric:tabular-nums}
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
svg text{fill:var(--text-secondary);font:12px "Readex",system-ui,-apple-system,"Segoe UI",sans-serif}
code{font-size:.9em}a:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
svg .strong{fill:var(--text-primary);font-weight:600}
.hit{fill:transparent;cursor:default;outline:none}.mark.on{filter:brightness(1.15)}svg text{pointer-events:none}
.card p.note{margin:10px 0 0;color:var(--text-secondary);font-size:13.5px}.card h3.gap{margin-top:24px}table.fit{min-width:0}#hf-section td.n{white-space:nowrap}
</style>"""

HELPERS = r"""const D=JSON.parse(document.getElementById("data").textContent);
const NS="http://www.w3.org/2000/svg";
const fmt=(v,d=0)=>v.toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d}).replace("-","−");
const pct=(v,d=1)=>fmt(100*v,d)+"%";
function el(tag,attrs={},parent){const e=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(parent)parent.append(e);return e}
function text(parent,x,y,s,attrs={}){const t=el("text",{x,y,...attrs},parent);t.textContent=s;return t}
function h(tag,s,cls){const e=document.createElement(tag);if(s!==undefined)e.textContent=s;if(cls)e.className=cls;return e}
function table(id,head,rows,numeric){const t=document.getElementById(id);t.replaceChildren();const tr=h("tr");
  head.forEach((c,i)=>{const th=h("th",c);if(numeric[i])th.className="n";tr.append(th)});t.append(tr);
  rows.forEach(r=>{const row=h("tr");r.forEach((c,i)=>{const td=h("td",c);if(numeric[i])td.className="n";row.append(td)});t.append(row)})}
const frac=v=>{const[a,b]=String(v).split("/");return b?Number(a)/Number(b):Number(a)};"""

HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
%%STYLE%%
</head>
<body>
<div class="viz-root"><header class="bar"><div><a class="brand" href="https://komail6222-png.github.io/mizan/">MIZAN</a><a class="back" href="%%BACK_HREF%%">%%BACK%%</a></div></header><main>
"""

MODELS_LINK = r"""<section id="models-link">
<h2>Foundation models from Hugging Face</h2>
<p class="lede">Pinned Hugging Face checkpoints, each measured in place of the SANAD retriever, the BASIRA descriptor or the RASID forecaster on the same generated data, with paired intervals for the forecast comparisons, have their own page: <a href="models.html">Foundation models from Hugging Face</a>.</p>
</section>
"""

VISA_LINK = r"""<section id="visa-link">
<h2>BASIRA on real images</h2>
<p class="lede">The inspection engine, unchanged, on real products from the public VisA dataset (CC BY 4.0), with a protocol sealed before any test image was scored, has its own page: <a href="visa/index.html">BASIRA on real images</a>.</p>
</section>
"""

PAGE = r"""<div class="kicker">Release 3.0 results, evaluated on generated fixtures</div>
<h1>Evaluation report</h1>
<p class="lede">What the three systems decide on held-out generated data, what the statistics promise, and where the evidence is still thin. Every number on this page comes from the JSON files under <code>reports/</code>.</p>
<div class="tiles" id="tiles"></div>

<h2>SANAD: procurement evidence</h2>
<p class="lede">Each set is a regression suite for one grammar version. A false MEETS means the system approved a requirement it should not have. The baselines never abstain.</p>
<div class="card"><div class="scroll"><table id="sanad"></table></div></div>

<h2>BASIRA: what one calibration bank guarantees</h2>
<p class="lede" id="basira-lede"></p>
<div class="card"><h3>Banks whose long-run false-alert rate exceeds 5%</h3>
<p class="sub" id="basira-sub"></p><div class="legend"><span style="--c:var(--series-1)">Split conformal (shipped rule)</span><span style="--c:var(--series-2)">PAC rule, δ = 10%</span><span class="line">10% bound (δ)</span></div>
<div class="chart" id="banks"></div>
<details><summary>Show data</summary><div class="scroll"><table id="banks-table"></table></div></details></div>

<h2>RASID: decision quality, not only forecast error</h2>
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
<h2>Safety evidence: properties, simulation and mutants</h2>
<p class="lede">Generated inputs and seeded schedules check relations that must hold for every case. Each suite is also run against deliberately broken versions of the system; a suite that cannot catch those proves nothing. Source: <code>reports/safety/safety.json</code>.</p>
<div class="card"><div class="scroll"><table id="safety"></table></div></div>
</section>
%%MODELS_LINK%%<p class="foot" id="foot"></p>
</main></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
%%HELPERS%%
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

function ledes(){const R=D.rasid,g=R.mae_gap,sg=v=>(v>0?"+":"")+fmt(v,3);
  document.getElementById("rasid-lede").textContent=`The ridge forecast's mean load error is ${fmt(R.mae[0],3)} kW against ${fmt(R.mae[1],3)} kW for the seasonal baseline over ${R.days} rolling days${g?`; per day the difference is ${sg(g.d)} kW (95% interval ${sg(g.ci[0])} to ${sg(g.ci[1])}), ${g.ci[0]>0||g.ci[1]<0?"resolved":"not resolved"}`:""}. Release 2.1 reported that the planner cost more than the seasonal baseline. That comparison mixed two effects: the forecast and the risk weight. Separating them shows what each one buys.`;
  const e=D.environment;document.getElementById("foot").textContent=`Generated from ${["evaluation",D.safety&&"safety",R.mae_gap&&"hf/rasid_intervals.json"].filter(Boolean).map(s=>"reports/"+s).join(", ")} · evaluation run: Python ${e.python||"?"} · NumPy ${e.numpy||"?"} · SciPy ${e.scipy||"?"} · All inputs are generated fixtures; costs are illustrative units. Rebuild with python scripts/build_report.py.`}
function safety(){if(!D.safety)return;document.getElementById("safety-section").hidden=false;
  table("safety",["Suite","What ran","Violations","Broken versions"],D.safety.map(r=>[r.suite,r.checks,String(r.violations),r.mutants]),[0,0,1,0])}
function render(){tiles();sanad();banks();share();tail();paired();ledes();safety()}
render();let t;addEventListener("resize",()=>{clearTimeout(t);t=setTimeout(()=>{banks();share();tail();paired()},120)});
</script>
</body>
</html>
"""

MODELS_PAGE = r"""<div class="kicker">Release 3.1 · generated fixtures</div>
<h1>Foundation models from Hugging Face</h1>
<section id="hf-section" hidden>
<p class="lede" id="hf-lede"></p>
<div class="card"><h3>BASIRA: inspection descriptor</h3><p class="sub" id="hf-basira-sub"></p>
<div class="scroll"><table class="fit" id="hf-basira"></table></div><p class="note" id="hf-basira-note"></p></div>
<div class="card"><h3>RASID: load and solar forecaster</h3><p class="sub" id="hf-rasid-sub"></p>
<div class="scroll"><table class="fit" id="hf-rasid"></table></div>
<h3 class="gap">Paired differences in forecast accuracy</h3><p class="sub" id="hf-accuracy-sub"></p>
<div class="scroll"><table id="hf-accuracy"></table></div><p class="note" id="hf-rasid-note"></p>
<h3 class="gap">Paired differences in mean daily cost</h3><p class="sub" id="hf-paired-sub"></p>
<div class="scroll"><table id="hf-paired"></table></div><p class="note" id="hf-paired-note"></p>
<h3 class="gap">Risk-weighted plans: cost and tail</h3>
<div class="scroll"><table class="fit" id="hf-tail"></table></div><p class="note" id="hf-tail-note"></p></div>
<div class="card"><h3>SANAD: evidence retriever</h3><p class="sub" id="hf-sanad-sub"></p>
<div class="scroll"><table class="fit" id="hf-sanad"></table></div>
<div class="scroll"><table class="fit" id="hf-sanad-lang"></table></div><p class="note" id="hf-sanad-note"></p></div>
</section>
<p class="foot" id="foot"></p>
</main></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
%%HELPERS%%
function models(){const F=D.hf;document.getElementById("hf-section").hidden=false;
  const put=(id,s)=>{document.getElementById(id).textContent=s},name=r=>r.repo.split("/")[1],sgn=v=>(v>0?"+":"")+fmt(v,3),
    list=xs=>xs.length<3?xs.join(" and "):xs.slice(0,-1).join(", ")+" and "+xs[xs.length-1],ident=r=>`${r.repo} (revision ${r.revision})`,
    off=[F.basira,F.rasid,F.sanad].filter(r=>!(r.gate&&r.identical)).map(r=>r.repo),ci=([x,y],g=sgn)=>`${g(x)} to ${g(y)}`,
    is=xs=>xs.length===1?"is":"are",s=xs=>xs.length===1?"":"s";
  put("hf-lede",`Three pinned checkpoints, each used frozen in place of one component and scored on the same items, seeds, origins and metrics as that component. ${F.fixture_only?"All inputs are generated fixtures. ":""}${off.length?`The fidelity gate or repeat run failed for ${list(off)}.`:"Every run passed its fidelity gate, which re-checks the current component in the new harness, and repeated identically."} Source: reports/hf/*.json.`);
  const B=F.basira,b=[B.baseline,B.model],s0=B.baseline.shipped,t=B.baseline.frozen_test,a=pct(frac(B.alpha),0),odds=b.map(x=>pct(x.shipped.p_worse));
  put("hf-basira-sub",`The shipped descriptor next to ${ident(B)}, on the same ${t.intact+t.defects} frozen items and calibration pools, seeds and draws.`);
  table("hf-basira",["Measure","Shipped descriptor",name(B)],[["Defects detected, frozen test",...b.map(x=>`${x.frozen_test.detected} / ${x.frozen_test.defects}`)],
    ["False alerts on intact rings, frozen test",...b.map(x=>`${x.frozen_test.false_alerts} / ${x.frozen_test.intact}`)],
    [`Shipped bank (m = ${s0.m}): long-run false-alert rate`,...b.map(x=>pct(x.shipped.rate))],
    ["How often a bank this bad or worse occurs",...odds],
    ...B.baseline.study.map((r,i)=>[`Study, m = ${r.m}: mean false alerts / banks above ${a}`,...b.map(x=>`${pct(x.study[i].false_alert)} / ${pct(x.study[i].share_above)}`)])],[0,1,1]);
  const rows=B.baseline.study.map((r,i)=>[r,B.model.study[i]]),gap=Math.max(...rows.map(([x,y])=>Math.abs(x.false_alert-y.false_alert))),
    at=f=>rows.filter(([x,y])=>f(y.share_above,x.share_above)).map(([x])=>x.m),
    share=[[at((y,x)=>y<x),"lower"],[at((y,x)=>y>x),"higher"]].filter(([m])=>m.length).map(([m,w])=>`${w} at m = ${list(m)}`),det=Math.min(...b.map(x=>x.detection)),near=100*gap<=B.precision_points;
  put("hf-basira-note",`The frozen test and the long-run rate rest on one bank of ${s0.m} parts: a bank this bad or worse occurs ${odds[0]} of the time for the shipped descriptor and ${odds[1]} for ${name(B)}. Over ${fmt(B.draws)} banks per size, under the shipped split-conformal rule, the mean false-alert rates are ${near?"roughly equal":"apart"}: they differ by at most ${fmt(100*gap,1)} percentage points, ${near?"within":"beyond"} the ±${B.precision_points}-point precision the study states for each rate.${near?` That is what split-conformal calibration predicts for any descriptor, so it is no evidence about ${name(B)} itself.`:""} The share of banks above ${a} with ${name(B)} is ${list(share)||"unchanged"}; no interval is reported for these shares. ${det===1?"Detection in the study stayed at 100% for both, so it does not separate them.":`Mean detection in the study fell to ${pct(det)}.`}`);
  const R=F.rasid,f=R.forecast,lv=R.levels,w=lv[lv.length-1]-lv[0],nom=pct(w,0),L=list(lv.map(String));
  put("hf-rasid-sub",`The shipped ridge forecaster next to ${ident(R)}, on the same ${R.days} rolling days, planner settings and verifier. Lower is better except coverage, whose target is ${nom}.`);
  table("hf-rasid",["Measure","Ridge forecaster",name(R)],[["Load MAE (kW)",...f.mae.map(v=>fmt(v,3))],[`Mean pinball loss, levels ${L}`,...f.pinball.map(v=>fmt(v,3))],
    [`Truncated CRPS, levels ${L}`,...f.crps.map(v=>fmt(v,3))],[`${nom} interval coverage of load`,...f.coverage.map(v=>pct(v))],
    ...[["risk-neutral","neutral"],["risk-weighted","weighted"]].map(([p,k])=>[`Total cost over ${R.days} days, ${p} planner`,...R.cost[k].map(v=>fmt(v,2))])],[0,1,1]);
  const ACC={load_mae:["Load MAE (kW)","load MAE",sgn,""],mean_pinball:["Mean pinball loss","pinball loss",sgn,""],truncated_crps:["Truncated CRPS","CRPS",sgn,""],
    coverage_80_load:[`${nom} interval coverage`,`${nom} coverage`,v=>(v>0?"+":"")+fmt(100*v,1)," points"]},
    read=([x,y])=>x>0?"Higher, excludes zero":y<0?"Lower, excludes zero":"Not resolved, spans zero",
    res=R.accuracy.filter(x=>x.ci[0]>0||x.ci[1]<0).map(x=>ACC[x.k][1]),un=R.accuracy.filter(x=>!(x.ci[0]>0||x.ci[1]<0)).map(x=>ACC[x.k][1]);
  table("hf-accuracy",["Measure","Mean daily difference","95% interval","Reading"],R.accuracy.map(x=>{const[l,,g,u]=ACC[x.k];return[l,g(x.d)+u,ci(x.ci,g)+u,read(x.ci)]}),[0,1,1,0]);
  put("hf-accuracy-sub",`${name(R)} minus ridge, day by day over the same ${R.days} days. ${res.length?`The ${list(res)} difference${s(res)} ${is(res)} resolved`:"No accuracy difference is resolved"}${un.length?`; the ${list(un)} difference${s(un)} ${is(un)} not`:""}.`);
  put("hf-rasid-note",`The accuracy intervals use the same bootstrap as the cost comparisons (${R.bootstrap}) on the per-day values in reports/hf/rasid.json, and are stored in reports/hf/rasid_intervals.json. Pinball loss and CRPS use the shared levels ${L}, so the ridge values are not those in reports/evaluation/rasid.json. Ridge costs are this run's, at most ${pct(R.shipped_gap,3)} from the evaluation report's RASID section, ${R.shipped_gap<=R.tolerance?"within":"outside"} the gate's ${pct(R.tolerance,2)} solver tolerance. ${fmt(R.pv_clipped)} negative solar values from ${name(R)} were clipped to zero before planning; solar is not scored. ${R.verified===R.days?"Every plan passed the independent verifier.":`One policy had only ${R.verified} of ${R.days} plans verified.`}`);
  const rd=([x,y])=>x>0?"Costlier, excludes zero":y<0?"Cheaper, excludes zero":"Not resolved, spans zero",
    key=R.paired.find(p=>p.policy==="chronos_risk_neutral"&&p.reference==="candidate_risk_neutral");
  table("hf-paired",["Comparison","Mean daily difference","95% interval","Days cheaper / costlier","Reading"],
    R.paired.map(p=>[p.label,sgn(p.mean_daily_difference),ci(p.mean_daily_difference_95ci),`${p.days_cheaper} / ${p.days_more_expensive}`,rd(p.mean_daily_difference_95ci)]),[0,1,1,1,0]);
  if(key){const c=key.mean_daily_difference_95ci,r=c[0]>0||c[1]<0;put("hf-paired-sub",`The forecast value of ${name(R)} over the ridge forecast, with the same risk-neutral planner, ${r?"is resolved":`is not resolved by these ${R.days} days`}: its 95% interval, ${ci(c)} per day, ${r?"excludes":"spans"} zero.`)}
  put("hf-paired-note",`Each difference is the ${name(R)} policy minus the reference; negative means ${name(R)} cost less. 95% intervals from a bootstrap: ${R.bootstrap}. Several 95% intervals are shown and none is adjusted for multiple comparisons.`);
  const T=R.tail,P=R.premium,K=R.planner,cl=R.chronos_levels,cmp=(x,y)=>x<y?"lower":x>y?"higher":"equal";
  table("hf-tail",["Risk-weighted plan","Ridge forecaster",name(R)],[[`Total cost over ${R.days} days`,...R.cost.weighted.map(v=>fmt(v,2))],
    ["Mean of the worst 10% of days",...T.cvar.map(v=>fmt(v,2))],["Worst day",...T.worst.map(v=>fmt(v,2))],
    ["Premium over the same forecaster's risk-neutral plan, per day",...[P.ridge,P.chronos].map(p=>sgn(p.d))]],[0,1,1]);
  put("hf-tail-note",`The risk-weighted planner minimises ${fmt(1-K.risk,2)} × expected cost + ${fmt(K.risk,2)} × CVaR of scenario cost at β = ${fmt(K.beta,2)}. ${K.costliest?`Every ${name(R)} scenario weighs at least 1 − β (${[...new Set(K.weights)].map(v=>fmt(v,1)).join(", ")}), so its CVaR term is the cost of the costliest scenario alone, and the fixed scenario rule (${R.pairing}) holds each path at one quantile level for all 24 hours, the most extreme pairing load at ${fmt(Math.max(...cl),1)} with solar at ${fmt(Math.min(...cl),1)}. `:""}The ridge plans use reduced paths of past days' forecast errors instead, so this comparison measures that scenario rule as well as the forecaster. The premium's 95% intervals: ridge ${ci(P.ridge.ci)}, ${name(R)} ${ci(P.chronos.ci)} per day. The ${name(R)} plan's realised tail is ${cmp(T.cvar[1],T.cvar[0])} on the worst 10% of days and ${cmp(T.worst[1],T.worst[0])} on the worst day; no interval is reported for these.`);
  const S=F.sanad,d=S.documents,first=k=>{const x=S.lexical[k][0],y=S.e5[k][0];return y<x?"less often":y>x?"more often":"as often"},bi=first("bilingual");
  put("hf-sanad-sub",`The engine's lexical retriever next to ${ident(S)}, each ranking every line of the queried datasheet. ${d.en+d.ar} generated datasheets (${d.en} English, ${d.ar} Arabic) give ${fmt(S.queries)} ${S.unit}.`);
  table("hf-sanad",["Query, metric","Lexical (shipped)",name(S)],[["bilingual","Bilingual (the engine's)"],["en","English label only"],["ar","Arabic label only"]].flatMap(([k,q])=>
    ["recall@1","recall@3","MRR@4"].map((l,i)=>[`${q}, ${l}`,...[S.lexical,S.e5].map(x=>i>1?fmt(x[k][i],3):pct(x[k][i]))])),[0,1,1]);
  const G=S.lang,n=[S.by_language.en,S.by_language.ar,S.by_language.ar,S.by_language.en],
    LAB=["English label, English datasheets","English label, Arabic datasheets","Arabic label, Arabic datasheets","Arabic label, English datasheets"];
  table("hf-sanad-lang",["Recall@1 by datasheet language (pairs)","Lexical (shipped)",name(S)],LAB.map((l,i)=>[`${l} (${n[i]})`,pct(G.lexical[i]),pct(G.e5[i])]),[0,1,1]);
  const all=(ix,f)=>ix.every(i=>f(G.e5[i],G.lexical[i])),lt=(x,y)=>x<y,gt=(x,y)=>x>y,same=[0,2],cross=[1,3],
    word=ix=>all(ix,lt)?"less often":all(ix,gt)?"more often":"not consistently more or less often",
    gain=first("en")==="more often"&&first("ar")==="more often"&&all(same,lt)&&all(cross,gt);
  put("hf-sanad-note",`With the bilingual query, ${name(S)} ranks the claim line first ${bi} ${bi==="as often"?"as":"than"} the lexical retriever, which is near its ceiling there: the grammar accepts only lines that start with the exact attribute label, and that query is exactly those labels. ${first("en")===first("ar")?`With either label alone it ranks the claim first ${first("en")}`:`With the English label alone it ranks the claim first ${first("en")}, with the Arabic label alone ${first("ar")}`}. By datasheet language, it ranks the claim first ${word(same)} in the query's own language and ${word(cross)} across languages, a query the engine never makes because it always joins both labels${gain?"; the one-label gain is entirely cross-lingual":""}. The ${fmt(S.queries)} pairs share ${d.en+d.ar} datasheets, the same few query strings and one fixed pool of near-miss lines per language, so they are not independent, and the per-pair results are not in the evidence: no interval is reported for these rates. Verdicts are not recomputed from either ranking.`);
  const e=F.environment;put("foot",`Generated from reports/hf (basira.json, rasid.json, rasid_intervals.json, sanad.json) · evaluation run: Python ${e.python} · NumPy ${e.numpy} · SciPy ${e.scipy} · torch ${e.torch} · All inputs are generated fixtures; costs are illustrative units. Rebuild with python scripts/build_report.py.`)}
models();
</script>
</body>
</html>
"""

FONT = ROOT / "mizan/web/fonts/ReadexPro-latin.woff2"  # Latin-only subset; the report has no Arabic text


def _page(body, data, title, back, back_href):
    data = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    head = HEAD.replace("%%TITLE%%", title).replace("%%BACK%%", back).replace("%%BACK_HREF%%", back_href)
    page = (head + body).replace("%%STYLE%%", STYLE).replace("%%HELPERS%%", HELPERS)
    page = page.replace("%%FONT_WOFF2%%", base64.b64encode(FONT.read_bytes()).decode())
    return page.replace("%%DATA%%", data)


def build(source, out, models_out=None, hf_source=None):
    """Write the report to out and, when there is Hugging Face evidence, the models page to models_out (default: a
    models.html beside out), linked from the report. Returns out."""
    models = models_summary(source, hf_source)
    visa = (Path(source).parent / "visa" / "index.html").exists()   # scripts/build_visa_report.py, served at report/visa/
    body = PAGE.replace("%%MODELS_LINK%%", (MODELS_LINK if models else "") + (VISA_LINK if visa else ""))
    Path(out).write_text(_page(body, summarize(source), "MIZAN | Evaluation report", "What is MIZAN?", "https://komail6222-png.github.io/mizan/"), encoding="utf-8")
    if models:
        target = Path(models_out) if models_out else Path(out).with_name("models.html")
        target.write_text(_page(MODELS_PAGE, models, "MIZAN | Foundation models from Hugging Face", "Evaluation report", "index.html"), encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=ROOT / "reports/evaluation")
    parser.add_argument("--out", default=ROOT / "reports/evaluation/index.html")
    parser.add_argument("--models-out", help="models page (default: models.html beside --out)")
    args = parser.parse_args()
    print(build(args.source, args.out, args.models_out))
