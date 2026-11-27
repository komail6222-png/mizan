"""Render RASID on real load (OPSD) from reports/opsd/rasid.json: one page.

    .venv/bin/python scripts/build_opsd_report.py          # writes reports/opsd/index.html

The page reuses the evaluation report's style and helpers (scripts/build_report.py). Every figure, interval and
reading comes from the embedded evidence, and the protocol's numbers from its sealed settings; nothing is typed by
hand. A record whose gate did not pass, whose repeat run is missing or not identical, or that is a quick run, is
refused.
"""
import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan.opsd import ATTRIBUTION  # noqa: E402
from scripts.build_report import _page, fail  # noqa: E402

POLICIES = ("candidate", "candidate_risk_neutral", "seasonal_naive", "perfect_foresight", "no_battery", "chronos",
            "chronos_risk_neutral")
LICENCE = "https://creativecommons.org/licenses/by/4.0/"
SOURCE = "https://data.open-power-system-data.org/household_data/2020-04-15/"


def _r(v, digits=6):
    return None if v is None else round(float(v), digits)


def _checked(record):
    require = lambda ok, msg: ok or fail(msg)  # noqa: E731
    require((record.get("gate") or {}).get("passed") is True, "reports/opsd/rasid.json: the fidelity gate did not pass")
    require((record.get("repeat_run") or {}).get("identical") is True,
            "reports/opsd/rasid.json: the repeat run is missing or not identical")
    require(record.get("quick") is False, "reports/opsd/rasid.json: a quick run is a smoke test, not evidence")
    return record


def _interval(x):
    return [_r(x["ci95"][0]), _r(x["ci95"][1])]


def summary(record):
    """What the page embeds: per-site sizing, counts, accuracy, decision quality, shares, comparisons, plans; pooled."""
    _checked(record)
    v = record["settings"]["values"]
    sites = {}
    for site, s in record["sites"].items():
        c, f = s["counts"], s["forecast"]
        sites[site] = {
            "building": s["building"], "scale": _r(s["base"]["scale"], 4), "limit": _r(s["base"]["limit_kw"], 1),
            "battery": {k: _r(x, 3) for k, x in s["base"]["battery"].items()},
            "counts": {"clean": c["clean_days"], "dst": c["daylight_saving_excluded"], "lookback": c["lookback_excluded"],
                       "ridge": sum(c["ridge_refused"].values()), "refused_days": len({r["date"] for r in c["planning_refused"]}),
                       "target_days": c["target_days"], "runs": c["runs"]},
            "forecast": {k: _r(f[k]) for k in ("mean_load_kw", "ridge_mae_kw", "seasonal_naive_mae_kw", "chronos_mae_kw",
                                               "ridge_mae_share_of_load", "seasonal_naive_mae_share_of_load",
                                               "chronos_mae_share_of_load", "ridge_mean_pinball", "chronos_mean_pinball",
                                               "ridge_truncated_crps", "chronos_truncated_crps", "ridge_coverage_80_load",
                                               "chronos_coverage_80_load")},
            "decision": {k: {"total": _r(d["total_cost"], 3), "regret": _r(d["regret_vs_perfect_foresight"], 3),
                             "worst": _r(d["worst_day_cost"], 3), "tail": _r(d["mean_worst_10pct_days"], 3)}
                         for k, d in s["decision_quality"].items()},
            "share": {k: {"value": _r(x["value"]), "ci95": _interval(x)} for k, x in s["share"].items()},
            "paired": [{"a": p["a"], "b": p["b"], "mean": _r(p["mean"]), "ci95": _interval(p), "reading": p["reading"]} for p in s["paired"]],
            "accuracy": [{"a": p["a"], "b": p["b"], "mean": _r(p["mean"]), "ci95": _interval(p), "reading": p["reading"]} for p in s["accuracy"]],
            "plans": s["plans"],
        }
    pooled = record["pooled"]
    env = record["environment"]
    return {
        "sites": sites,
        "pooled": {"share": {k: {"value": _r(x["mean"]), "ci95": _interval(x), "reading": x["reading"]} for k, x in pooled["share"].items()},
                   "paired": [{"a": p["a"], "b": p["b"], "mean": _r(p["mean"]), "ci95": _interval(p), "reading": p["reading"]}
                              for p in pooled["paired"]],
                   "accuracy": [{"a": p["a"], "b": p["b"], "mean": _r(p["mean"]), "ci95": _interval(p), "reading": p["reading"]}
                                for p in pooled["accuracy"]]},
        "cfg": {"block_days": v["block_days"], "draws": v["draws"], "seed": v["seed"], "lookback_days": v["lookback_days"],
                "history_days": v["history_days"], "sizing_days": v["sizing_days"]},
        "gate": {"ridge": len(record["gate"]["ridge"]["checked"]), "chronos": len(record["gate"]["chronos"]["checked"])},
        "repeat": record["repeat_run"]["process"], "seal": record["seal_commit"], "digest": record["protocol_digest"],
        "env": {k: env.get(k) for k in ("python", "numpy", "scipy", "torch", "cpu")}, "elapsed": record["elapsed_seconds"],
    }


ATTRIBUTION_HTML = (f'<p class="lede" id="opsd-attribution">{html.escape(ATTRIBUTION, quote=False)} '
                    f'Licence: <a href="{LICENCE}">{LICENCE}</a>. Data: <a href="{SOURCE}">data.open-power-system-data.org</a>.</p>')

PAGE = r"""<div class="kicker">Release 3.2 · real public measurements (OPSD)</div>
<h1>RASID on real load</h1>
<p class="lede" id="opsd-lede"></p>
<section id="opsd-section">
<div class="card"><h3>The two sites</h3><p class="sub" id="opsd-sites-sub"></p>
<div class="scroll"><table id="opsd-sites"></table></div></div>
<div class="card"><h3>Which days were planned</h3><p class="sub" id="opsd-days-sub"></p>
<div class="scroll"><table id="opsd-days"></table></div></div>
<div class="card"><h3>Forecast accuracy on load</h3><p class="sub" id="opsd-accuracy-sub"></p>
<div class="scroll"><table id="opsd-accuracy"></table></div>
<h3 class="gap">Forecasters compared on the same days</h3>
<div class="scroll"><table id="opsd-accuracy-paired"></table></div></div>
<div class="card"><h3>Planning quality</h3><p class="sub" id="opsd-decision-sub"></p><div id="opsd-decision"></div></div>
<div class="card"><h3>Policies compared on the same days</h3><p class="sub" id="opsd-paired-sub"></p>
<div class="scroll"><table id="opsd-paired"></table></div><p class="note" id="opsd-paired-note"></p></div>
<div class="card"><h3>Both sites together</h3><p class="sub" id="opsd-pooled-sub"></p>
<div class="scroll"><table class="fit" id="opsd-share"></table></div>
<h3 class="gap">Policies compared, both sites</h3><div class="scroll"><table id="opsd-pooled"></table></div>
<h3 class="gap">Forecasters compared, both sites</h3><div class="scroll"><table id="opsd-pooled-accuracy"></table></div></div>
<h2>Data</h2>
%%ATTRIBUTION%%
</section>
<p class="foot" id="foot"></p>
</main></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
"use strict";
%%HELPERS%%
const C=D.cfg,S=Object.keys(D.sites).sort();
const P={"candidate":"Ridge, risk-aware","candidate_risk_neutral":"Ridge, risk-neutral","seasonal_naive":"Seasonal baseline","perfect_foresight":"Hindsight (reference)","no_battery":"No battery","chronos":"Chronos, risk-aware","chronos_risk_neutral":"Chronos, risk-neutral"};
const A={"ridge_mae":"Ridge","naive_mae":"Seasonal baseline","chronos_mae":"Chronos median"};
const B=s=>`${s} (${D.sites[s].building})`;
const put=(id,s)=>{document.getElementById(id).textContent=s};
const W={"higher":"Higher, excludes zero","lower":"Lower, excludes zero","not resolved":"Not resolved, includes zero"};
const units=v=>(v>0?"+":"")+fmt(v,3);
const share=x=>x.value===null?"–":`${pct(x.value)} (${pct(x.ci95[0])} to ${pct(x.ci95[1])})`;
const T=S.reduce((a,s)=>a+D.sites[s].counts.target_days,0);
put("opsd-lede",`RASID's unchanged forecaster, planner and independent verifier on ${S.length} real business sites from the Open Power System Data household package (Konstanz, Germany): ${fmt(T)} days planned. The load of each site is reconstructed (grid import + solar); the no-export check found no sunny clean hour with near-zero import. The hypothetical battery is scaled to each site; prices are RASID's illustrative tariff, unchanged, so costs are in illustrative tariff units. The protocol was sealed at commit ${D.seal.slice(0,12)} before any site day was forecast; the harness first reproduced the generated site's published results (${fmt(D.gate.ridge)} and ${fmt(D.gate.chronos)} checks), and the whole run repeated identically in ${D.repeat}.`);
table("opsd-sites",["Site","Scale","Battery","Grid limit","Days planned","Runs"],S.map(s=>{const x=D.sites[s];return[B(s),fmt(x.scale,3),`${fmt(x.battery.capacity,1)} kWh, ${fmt(x.battery.power,1)} kW`,`${fmt(x.limit,0)} kW`,fmt(x.counts.target_days),fmt(x.counts.runs)]}),[0,1,1,1,1,1]);
put("opsd-sites-sub",`The shipped settings scaled by each site's mean load over its first ${C.sizing_days} clean days; the grid limit never binds. Prices, risk weight and efficiencies are unchanged.`);
table("opsd-days",["Site","Clean days","Daylight-saving days","Fewer than "+C.lookback_days+" clean days before","Ridge refused","Dropped by a refusal","Planned"],S.map(s=>{const c=D.sites[s].counts;return[s,fmt(c.clean),fmt(c.dst),fmt(c.lookback),fmt(c.ridge),fmt(c.refused_days),fmt(c.target_days)]}),[0,1,1,1,1,1,1]);
put("opsd-days-sub",`A clean day has every hour metered, no hour flagged as filled, is not a copy of the day before and has no straight-line fill. A day is planned when it has 24 hours and the ${C.lookback_days} days before it are clean. After any gap, each policy starts again from the initial charge.`+(D.copies?` The rule does not catch a day that repeats an earlier week: ${S.map(s=>`${fmt(D.copies.target_days[s])} planned days at ${s}`).join(" and ")} repeat, hour by hour, the day one or two weeks earlier. A separate check without them (reports/opsd/copies.json) ${D.copies.same_readings?"leaves every reading on this page the same":"changes at least one reading on this page"}.`:""));
table("opsd-accuracy",["Site","Forecaster","Load error (kW)","Share of mean load","Pinball loss","80% coverage"],S.flatMap(s=>{const f=D.sites[s].forecast;return[[s,"Ridge",fmt(f.ridge_mae_kw,3),pct(f.ridge_mae_share_of_load),fmt(f.ridge_mean_pinball,3),pct(f.ridge_coverage_80_load)],[s,"Seasonal baseline",fmt(f.seasonal_naive_mae_kw,3),pct(f.seasonal_naive_mae_share_of_load),"–","–"],[s,"Chronos median",fmt(f.chronos_mae_kw,3),pct(f.chronos_mae_share_of_load),fmt(f.chronos_mean_pinball,3),pct(f.chronos_coverage_80_load)]]}),[0,0,1,1,1,1]);
put("opsd-accuracy-sub","Mean absolute error of the day-ahead load forecast over the planned days; pinball loss on the 0.1, 0.5 and 0.9 quantiles; coverage of the 80% range.");
const pair=(rows,unit)=>rows.map(p=>[`${(A[p.a]||P[p.a])} minus ${(A[p.b]||P[p.b])}`,unit(p.mean),`${unit(p.ci95[0])} to ${unit(p.ci95[1])}`,W[p.reading]]);
table("opsd-accuracy-paired",["Comparison","Site","Mean difference (kW)","95% interval","Reading"],S.flatMap(s=>pair(D.sites[s].accuracy,units).map(r=>[r[0],s,...r.slice(1)])),[0,0,1,1,0]);
const box=document.getElementById("opsd-decision");box.replaceChildren();
S.forEach((s,i)=>{const sc=h("div",undefined,"scroll"),t=h("table");t.id="opsd-decision-"+i;box.append(h("h3",B(s),"gap"));sc.append(t);box.append(sc);
  table(t.id,["Policy","Total cost","Above hindsight","Share of achievable battery value","Worst day","Worst 10% of days","Plans verified"],Object.keys(P).map(k=>{const d=D.sites[s].decision[k],pl=D.sites[s].plans[k];return[P[k],fmt(d.total,1),fmt(d.regret,1),k==="no_battery"||k==="perfect_foresight"?"–":share(D.sites[s].share[k]),fmt(d.worst,1),fmt(d.tail,1),`${fmt(pl.verified_feasible)} of ${fmt(pl.kept_days+pl.rejected+pl.no_plan)}`]}),[0,1,1,1,1,1,1])});
put("opsd-decision-sub","Costs in illustrative tariff units over the planned days. The share of achievable battery value is the part of the hindsight plan's improvement over no battery that a policy captures; its interval comes from the block bootstrap below. Plans verified counts the plans the independent verifier accepted, out of every plan the policy made, rejections and days without a plan included. A day on which any policy's planner input was refused was dropped for every policy; it appears under “Dropped by a refusal” above.");
table("opsd-paired",["Comparison","Site","Mean daily difference","95% interval","Reading"],S.flatMap(s=>pair(D.sites[s].paired,units).map(r=>[r[0],s,...r.slice(1)])),[0,0,1,1,0]);
put("opsd-paired-sub",`Each difference compares two policies on exactly the same days, in illustrative tariff units per day; a negative difference means the first policy cost less. The interval comes from a bootstrap of ${C.block_days}-day blocks that never span a gap (${fmt(C.draws)} draws; seed ${S.map((s,i)=>`${C.seed+i} for ${s}`).join(", ")}).`);
put("opsd-paired-note","Many intervals are shown on this page and none is adjusted for multiple comparisons.");
table("opsd-share",["Policy","Share of achievable battery value (95% interval)"],Object.keys(P).filter(k=>k!=="no_battery"&&k!=="perfect_foresight").map(k=>[P[k],share(D.pooled.share[k])]),[0,1]);
table("opsd-pooled",["Comparison","Mean daily difference","95% interval","Reading"],pair(D.pooled.paired,v=>(v>0?"+":"")+pct(v)),[0,1,1,0]);
put("opsd-pooled-sub","Each site counts equally. Cost differences are divided by the site's mean daily cost without a battery, so they read as a share of that cost; forecast-error differences are divided by the site's mean load.");
table("opsd-pooled-accuracy",["Comparison","Mean difference","95% interval","Reading"],pair(D.pooled.accuracy,v=>(v>0?"+":"")+fmt(100*v,1)+" points"),[0,1,1,0]);
const e=D.env;put("foot",`Generated from reports/opsd/rasid.json · protocol digest ${D.digest.slice(0,12)} · run of ${fmt(D.elapsed/60,0)} min: Python ${e.python} · NumPy ${e.numpy} · SciPy ${e.scipy} · torch ${e.torch} · ${e.cpu}. Real public measurements, not MIZAN's own; load reconstructed (grid import + solar); costs in illustrative tariff units. Rebuild with python scripts/build_opsd_report.py.`);
</script>
</body>
</html>
"""


def copies_summary(record, copies):
    """The separate copy check (scripts/opsd_copy_check.py): per site, planned days that repeat an earlier week, and
    whether every reading of the sealed analysis stays the same without them."""
    def readings(x):
        out = [p["reading"] for s in sorted(x["sites"]) for p in x["sites"][s]["paired"] + x["sites"][s]["accuracy"]]
        return out + [p["reading"] for p in x["pooled"]["paired"] + x["pooled"]["accuracy"]]
    return {"target_days": {s: copies["sites"][s]["week_copies"]["target_days"] for s in sorted(copies["sites"])},
            "same_readings": readings(copies) == readings(record)}


def build(source=ROOT / "reports" / "opsd", out_dir=ROOT / "reports" / "opsd"):
    record = json.loads((Path(source) / "rasid.json").read_text(encoding="utf-8"))
    data = summary(record)
    copies_path = Path(source) / "copies.json"
    data["copies"] = copies_summary(record, json.loads(copies_path.read_text(encoding="utf-8"))) if copies_path.exists() else None
    target = Path(out_dir) / "index.html"
    body = PAGE.replace("%%ATTRIBUTION%%", ATTRIBUTION_HTML)
    target.write_text(_page(body, data, "MIZAN | RASID on real load", "Evaluation report", "../index.html"),
                      encoding="utf-8")
    return [target]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=ROOT / "reports" / "opsd")
    parser.add_argument("--out-dir", default=ROOT / "reports" / "opsd")
    args = parser.parse_args()
    for p in build(args.source, args.out_dir):
        print(p, p.stat().st_size)
