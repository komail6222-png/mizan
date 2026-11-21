"""Reproducible held-out synthetic evaluations. Numbers never stand in for factory or tariff data."""
from datetime import datetime,timedelta,timezone
import math,re,time
from fractions import Fraction
from pathlib import Path
import numpy as np
from . import examples
from .core.contract import canonical,digest
from .sanad import engine as s
from .basira import engine as b
from .rasid.forecast import records,forecast,quantile_scores
from .rasid.input import checked
from .rasid.solver import solve
from .rasid.verify import verify


def text_evaluation():
    recipes=[("20","20","MEETS"),("18","18","DOES_NOT_MEET"),("at least 21","على الاقل ٢١","MEETS"),
             ("up to 24","حتى ٢٤","REVIEW"),("optional 29","اختياري ٢٩","REVIEW"),("up to 16","حتى ١٦","DOES_NOT_MEET")]
    rows=[]
    for lang in (0,1):
        for i,(en,ar,truth) in enumerate(recipes):
            for offset in (0,1,2):
                model="Fixture-"+str(i*3+offset);text=("Model: " if not lang else "الطراز: ")+model+"\n"
                text+=s.ATTR["apparent_power"][lang]+": "+(ar if lang else en)+" kVA"+"\n"*offset
                doc={"name":"held-out-"+str(lang),"pages":[text]};p={"documents":[doc],"model":model,
                    "rules":[{"attribute":"apparent_power","op":"ge","unit":"kVA","threshold":"20"}]}
                res=s.evaluate(p,{"engine":s.PROFILE,"approved_sources":{s.source_id(doc):1}})
                result=res["requirements"][0]["verdict"]
                v=int("".join(x for x in s.normal(ar if lang else en) if x.isdigit()))
                rows.append(dict(language=["en","ar"][lang],pattern=i,case=offset,expected=truth,actual=result,baseline="MEETS" if v>=20 else "DOES_NOT_MEET"))
    return {"fixture_rows":len(rows),"distinct_language_template_pairs":12,"note":"Three layout/model-ID variants per template. This is a grammar regression set.",
            "correct":sum(r["expected"]==r["actual"] for r in rows),"naive_numeric_correct":sum(r["expected"]==r["baseline"] for r in rows),
            "false_meets":sum(r["actual"]=="MEETS" and r["expected"]!="MEETS" for r in rows),
            "decisive":sum(r["actual"]!="REVIEW" for r in rows),"rows":rows,"grammar_2_extension":text_extension(),"grammar_3_tables":text_tables()}


# Grammar-2 regression fixtures, written together with the change: they are not held-out evidence.
# (case, header lines, value line, lines the signed approval acknowledges, expected verdict for >= 20 kVA)
EXTENSION=[
    ("tatweel",[],"القدرة الظاهريـــة الاسمية: ٢٠ kVA",[],"MEETS"),
    ("arabic_kva_words",[],"القدرة الظاهرية الاسمية: ٢٠ كيلو فولت أمبير",[],"MEETS"),
    ("arabic_kva_words_low",[],"القدرة الظاهرية الاسمية: ١٨ كيلو فولت أمبير",[],"DOES_NOT_MEET"),
    ("arabic_thousands",[],"القدرة الظاهرية الاسمية: ٢٠٬٠٠٠ فولت أمبير",[],"MEETS"),
    ("arabic_thousands_low",[],"القدرة الظاهرية الاسمية: ١٦٬٠٠٠ فولت أمبير",[],"DOES_NOT_MEET"),
    ("arabic_decimal",[],"القدرة الظاهرية الاسمية: ٢٠٫٥ kVA",[],"MEETS"),
    ("ascii_thousands_ambiguous",[],"Rated apparent power: 20,000 VA",[],"REVIEW"),
    ("ascii_decimal_comma_ambiguous",[],"Rated apparent power: 20,5 kVA",[],"REVIEW"),
    ("foreign_digits",[],"Rated apparent power: ২০ kVA",[],"REVIEW"),
    ("title_unacknowledged",["UPS Technical Schedule"],"Rated apparent power: 20 kVA",[],"REVIEW"),
    ("title_acknowledged",["UPS Technical Schedule"],"Rated apparent power: 20 kVA",["UPS Technical Schedule"],"MEETS"),
    ("arabic_title_acknowledged",["جدول المواصفات الفنية"],"القدرة الظاهرية الاسمية: ٢٠ kVA",["جدول المواصفات الفنية"],"MEETS"),
    ("footer_acknowledged_low",["Page 1 of 2"],"Rated apparent power: 18 kVA",["Page 1 of 2"],"DOES_NOT_MEET"),
    ("derating_forged_ack",["Derated 10% above 40 °C"],"Rated apparent power: 20 kVA",["Derated 10% above 40 °C"],"REVIEW"),
    ("eco_mode_forged_ack",["Output limited to 16 kVA in eco mode"],"Rated apparent power: 20 kVA",["Output limited to 16 kVA in eco mode"],"REVIEW"),
    ("attribute_mention_forged_ack",["Rated apparent power varies by configuration"],"Rated apparent power: 20 kVA",
     ["Rated apparent power varies by configuration"],"REVIEW"),
]


# Grammar-3 table fixtures for the offered model Atlas-20 and the rule apparent power >= 20 kVA. Written with the change.
# (case, page, acknowledged lines, expected verdict)
HEAD="| Model | Atlas-10 | Atlas-20 | Atlas-30 |"
ROW="| Rated apparent power | {} | {} | {} |"
TABLE_CASES=[
    ("middle_column_meets",HEAD+"\n"+ROW.format("10 kVA","20 kVA","30 kVA"),[],"MEETS"),
    ("middle_column_low",HEAD+"\n"+ROW.format("10 kVA","16 kVA","30 kVA"),[],"DOES_NOT_MEET"),
    ("first_column_meets","| Model | Atlas-20 | Atlas-10 | Atlas-40 |\n"+ROW.format("25 kVA","15 kVA","40 kVA"),[],"MEETS"),
    ("last_column_low","| Model | Atlas-22 | Atlas-24 | Atlas-20 |\n"+ROW.format("22 kVA","24 kVA","18 kVA"),[],"DOES_NOT_MEET"),
    ("markdown_unit_column","| Model | Unit | Atlas-10 | Atlas-20 |\n|---|---|---|---|\n| Rated apparent power | kVA | 10 | 20 |",[],"MEETS"),
    ("arabic_right_to_left","| Atlas-30 | Atlas-20 | الطراز |\n| ١٦ kVA | ٢٠ kVA | القدرة الظاهرية الاسمية |",[],"MEETS"),
    ("lower_bound_cell",HEAD+"\n"+ROW.format("10 kVA","at least 21 kVA","30 kVA"),[],"MEETS"),
    ("upper_bound_cell",HEAD+"\n"+ROW.format("10 kVA","up to 24 kVA","30 kVA"),[],"REVIEW"),
    ("dash_cell",HEAD+"\n"+ROW.format("10 kVA","—","30 kVA"),[],"REVIEW"),
    ("footnote_marker",HEAD+"\n"+ROW.format("10 kVA","20 kVA*","30 kVA"),[],"REVIEW"),
    ("row_shape_error",HEAD+"\n| Rated apparent power | 10 kVA | 20 kVA |",[],"REVIEW"),
    ("duplicate_model_header","| Model | Atlas-20 | Atlas-20 |\n| Rated apparent power | 10 kVA | 20 kVA |",[],"REVIEW"),
    ("model_absent_from_table","| Model | Atlas-30 |\n| Rated apparent power | 30 kVA |",[],"REVIEW"),
    ("unscored_row_unacknowledged",HEAD+"\n"+ROW.format("10 kVA","20 kVA","30 kVA")+"\n| Input voltage | 230 V | 400 V | 400 V |",[],"REVIEW"),
    ("unscored_row_acknowledged",HEAD+"\n"+ROW.format("10 kVA","20 kVA","30 kVA")+"\n| Input voltage | 230 V | 400 V | 400 V |",
     ["| Input voltage | 230 V | 400 V | 400 V |"],"MEETS"),
    ("related_row_forged_ack",HEAD+"\n"+ROW.format("10 kVA","20 kVA","30 kVA")+"\n| Efficiency (eco mode) | 99 % | 99 % | 99 % |",
     ["| Efficiency (eco mode) | 99 % | 99 % | 99 % |"],"REVIEW"),
]


def naive_row_reader(page):
    """Baseline: the first number on the first line naming the attribute, read as kVA. It never abstains."""
    for line in page.splitlines():
        text=s.normal(line)
        if any(label in text for label in (s.normal(x) for x in s.ATTR["apparent_power"])):
            numbers=re.findall(r"[0-9]+(?:\.[0-9]+)?",text)
            if numbers:return "MEETS" if float(numbers[0])>=20 else "DOES_NOT_MEET"
    return "REVIEW"


def text_tables():
    rows=[]
    for case,page,acknowledged,truth in TABLE_CASES:
        doc={"name":"table-"+case,"pages":[page]}
        acks=[{"page":1,"start":page.index(q),"end":page.index(q)+len(q),"quote_sha256":s.quote_digest(q)} for q in acknowledged]
        p={"documents":[doc],"model":"Atlas-20","rules":[{"attribute":"apparent_power","op":"ge","unit":"kVA","threshold":"20"}]}
        got=s.evaluate(p,{"engine":s.PROFILE,"approved_sources":{s.source_id(doc):{"level":1,"acknowledged":acks} if acks else 1}})
        rows.append(dict(case=case,expected=truth,actual=got["requirements"][0]["verdict"],naive_row_reader=naive_row_reader(page)))
    return {"fixture_rows":len(rows),"correct":sum(r["expected"]==r["actual"] for r in rows),
            "false_meets":sum(r["actual"]=="MEETS" and r["expected"]!="MEETS" for r in rows),
            "decisive":sum(r["actual"]!="REVIEW" for r in rows),
            "naive_row_reader_correct":sum(r["expected"]==r["naive_row_reader"] for r in rows),
            "naive_row_reader_false_meets":sum(r["naive_row_reader"]=="MEETS" and r["expected"]!="MEETS" for r in rows),
            "rows":rows,"note":"Regression fixtures written with grammar 3. The naive reader takes the first number on the attribute row."}


def text_extension():
    rows=[]
    for case,header,line,acknowledged,truth in EXTENSION:
        arabic=any("؀"<=c<="ۿ" for c in line)
        page="\n".join([*header,("الطراز: " if arabic else "Model: ")+"Atlas-20",line])
        doc={"name":"extension-"+case,"pages":[page]}
        acks=[{"page":1,"start":page.index(q),"end":page.index(q)+len(q),"quote_sha256":s.quote_digest(q)} for q in acknowledged]
        approval={"level":1,"acknowledged":acks} if acks else 1
        p={"documents":[doc],"model":"Atlas-20","rules":[{"attribute":"apparent_power","op":"ge","unit":"kVA","threshold":"20"}]}
        got=s.evaluate(p,{"engine":s.PROFILE,"approved_sources":{s.source_id(doc):approval}})["requirements"][0]["verdict"]
        rows.append(dict(case=case,expected=truth,actual=got))
    return {"fixture_rows":len(rows),"correct":sum(r["expected"]==r["actual"] for r in rows),
            "false_meets":sum(r["actual"]=="MEETS" and r["expected"]!="MEETS" for r in rows),
            "decisive":sum(r["actual"]!="REVIEW" for r in rows),"rows":rows,
            "note":"Regression fixtures written with grammar 2, including forged acknowledgements that must stay REVIEW. Not held-out evidence."}


def vision_evaluation():
    p=examples.inspection();bank,cal,ident,_,_=b.fit(p["reference"],p["calibration"])
    ref=np.stack([np.asarray(b.decode_image(x["png"]),dtype=float) for x in p["reference"]]).mean(0)
    distance=lambda im:float(np.square(np.asarray(im,dtype=float)-ref).mean())
    c_base=sorted(distance(b.decode_image(x["png"])) for x in p["calibration"])
    rows=[]
    for i in range(80):
        defect=None if i<40 else ("pit" if i%2 else "crack");im=examples.part(9100+i,defect)
        score,_=b.score(b.features(im),bank);pred=b.rank_decision(cal,score,".05")
        baseline=b.rank_decision(c_base,distance(im),".05")
        rows.append(dict(seed=9100+i,condition=defect or "intact",p_value=pred["p_value"],flag=pred["decision"]=="FLAG",pixel_baseline_flag=baseline["decision"]=="FLAG"))
    return {"test_items":80,"synthetic_normals":40,"synthetic_defects":40,"calibration_m":len(cal),"bank_digest":ident,
            "detected":sum(x["flag"] for x in rows[40:]),"false_alerts":sum(x["flag"] for x in rows[:40]),
            "pixel_baseline_detected":sum(x["pixel_baseline_flag"] for x in rows[40:]),
            "pixel_baseline_false_alerts":sum(x["pixel_baseline_flag"] for x in rows[:40]),"rows":rows,
            "scope":"Generated aligned rings, two defect mechanisms, zero real labeled defect items. No industrial escape-rate certificate.",
            "calibration_study":calibration_study()}


def calibration_study(draws=400,sizes=(24,50,100),alpha=Fraction(1,20),delta=Fraction(1,10),seed=23,pools=(4000,4000,400)):
    """Repeat calibration many times on the fixed bank to separate the marginal guarantee from what one bank delivers.

    Normal pool A supplies calibration draws, independent normal pool B and a defect pool measure each threshold's
    long-run false-alert and detection rates. All pools are new generator seeds, disjoint from bank, calibration and
    the frozen 9100-9179 test items."""
    p=examples.inspection();bank,shipped,ident,_,_=b.fit(p["reference"],p["calibration"])
    def scores(seeds,defect=None):
        return np.array([b.score(b.features(examples.part(s,defect(s) if defect else None)),bank)[0] for s in seeds])
    pool_a=scores(range(30000,30000+pools[0]));pool_b=scores(range(40000,40000+pools[1]))
    defects=scores(range(50000,50000+pools[2]),lambda s:"pit" if s%2 else "crack")
    def rates(threshold):
        return (float(np.mean(pool_b>threshold)),float(np.mean(defects>threshold))) if threshold is not None else (0.,0.)
    m0=len(shipped);k0=math.ceil((m0+1)*(1-alpha));fa0,det0=rates(sorted(shipped)[k0-1])
    rng=np.random.default_rng(seed);rows=[]
    for m in sizes:
        k=math.ceil((m+1)*(1-alpha));kp=b.pac_rank(m,alpha,delta);marg=[];pac=[]
        for _ in range(draws):
            cal=np.sort(rng.choice(pool_a,m,replace=False))
            marg.append(rates(cal[k-1]));pac.append(rates(cal[kp-1]) if kp else (None,None))
        fa=np.array([x[0] for x in marg]);det=np.array([x[1] for x in marg])
        row={"m":m,"marginal_rank":k,"marginal_mean_false_alert":float(fa.mean()),"marginal_share_of_banks_above_alpha":float(np.mean(fa>float(alpha))),
             "marginal_theoretical_share_above_alpha":float(b.exceedance(m,k,alpha)),"marginal_mean_detection":float(det.mean()),
             "marginal_false_alert_p90":float(np.quantile(fa,.9)),"pac_rank":kp}
        if kp:
            fp=np.array([x[0] for x in pac]);dp=np.array([x[1] for x in pac])
            row.update(pac_mean_false_alert=float(fp.mean()),pac_share_of_banks_above_alpha=float(np.mean(fp>float(alpha))),
                       pac_theoretical_share_above_alpha=float(b.exceedance(m,kp,alpha)),pac_mean_detection=float(dp.mean()))
        rows.append(row)
    pool_a_max_pct=float(np.mean(pool_b<sorted(shipped)[k0-1]))
    return {"alpha":str(alpha),"delta":str(delta),"draws_per_size":draws,"bank_digest":ident,
            "pools":{"calibration_pool_normals":len(pool_a),"evaluation_normals":len(pool_b),"evaluation_defects":len(defects)},
            "shipped_calibration":{"m":m0,"rank":k0,"long_run_false_alert_estimate":fa0,"detection_estimate":det0,
                                   "threshold_percentile_among_normals":pool_a_max_pct,
                                   "probability_of_a_bank_at_least_this_bad":float(Fraction(pool_a_max_pct).limit_denominator(10**6)**m0),
                                   "exceedance_of_rule":float(b.exceedance(m0,k0,alpha))},
            "minimum_m_for_pac":b.minimum_m(alpha,delta),"rows":rows,
            "scope":("Generated rings only. Pools are finite samples, so each long-run rate is itself an estimate "
                     "(about ±0.35 percentage points at 5% for 4000 normals). Calibration sets are drawn without replacement from a 4000-part pool, which slightly understates bank-to-bank variance relative to fresh draws.")}


def realized(plan,target,settings):
    arr=np.array;dt=arr(settings["hours"]);rt=settings["tariff"];a=settings["assets"];load,pv=np.array(target).T
    flex=np.zeros(len(dt))
    for q in plan["interruptible"]:flex+=arr(q)
    for starts,block in zip(plan["block_start"],settings["blocks"]):
        i=int(np.argmax(starts));flex[i:i+block["length"]]+=block["power"]
    grid=np.maximum(0,load+flex+arr(plan["charge"])-arr(plan["discharge"])-pv)
    dev=grid-arr(plan["nomination"]);excess=np.maximum(grid-a["limit"],0)
    bill=np.sum(dt*(arr(rt["price"])*grid+arr(rt["up"])*dev.clip(0)+arr(rt["down"])*(-dev).clip(0)
                    +rt["wear"]*arr(plan["discharge"])+rt["overload"]*excess))
    peak=float(grid.max())
    return float(bill+rt["demand"]*max(0,peak-rt["established"])),peak


# Each policy keeps its own battery state and month-to-date peak. "candidate" and "seasonal_naive" are the 2.1 columns.
POLICIES=("candidate","candidate_risk_neutral","seasonal_naive","perfect_foresight","no_battery")


def policy_problem(name,f,y,state,peak):
    p=examples.settings(24);p["tariff"]["established"]=peak;p["assets"]["initial"]=state;p["assets"]["terminal"]=40
    weights,paths={"candidate":(f["weights"],f["scenarios"]),"candidate_risk_neutral":(f["weights"],f["scenarios"]),
                   "seasonal_naive":([1],[f["seasonal_naive"]]),"perfect_foresight":([1],[y.tolist()]),
                   "no_battery":([1],[f["seasonal_naive"]])}[name]
    p.update(hours=[1]*24,weights=weights,scenarios=paths)
    if name=="candidate_risk_neutral":p["risk"]=0
    if name=="no_battery":p["assets"].update(capacity=0,power=0,initial=0,terminal=0)
    return checked(p)


def tail_mean(values,share=.10):
    worst=sorted(values)[-max(1,math.ceil(len(values)*share)):]
    return math.fsum(worst)/len(worst)


def decision_summary(rows):
    total={k:math.fsum(r[k+"_cost"] for r in rows) for k in POLICIES}
    achievable=total["no_battery"]-total["perfect_foresight"]
    out={}
    for k in POLICIES:
        daily=[r[k+"_cost"] for r in rows]
        out[k]={"total_cost":total[k],"regret_vs_perfect_foresight":total[k]-total["perfect_foresight"],
                "battery_value":total["no_battery"]-total[k],
                "share_of_achievable_battery_value":(total["no_battery"]-total[k])/achievable if achievable>0 else None,
                "worst_day_cost":max(daily),"mean_worst_10pct_days":tail_mean(daily),
                "verified_feasible_plans":sum(r[k+"_status"]=="VERIFIED_FEASIBLE" for r in rows)}
    return out


def paired_comparison(rows,a,b,block=7,draws=4000,seed=2026):
    """Mean daily cost difference a-b with a moving-block bootstrap interval. Days are dependent through carried
    battery state and the monthly peak, so whole weeks are resampled rather than single days."""
    d=np.array([r[a+"_cost"]-r[b+"_cost"] for r in rows]);n=len(d)
    rng=np.random.default_rng(seed);block=min(block,n);starts=n-block+1;need=math.ceil(n/block)
    means=[np.concatenate([d[s:s+block] for s in rng.integers(0,starts,need)])[:n].mean() for _ in range(draws)]
    lo,hi=np.quantile(means,[.025,.975])
    return {"policy":a,"reference":b,"total_difference":float(d.sum()),"mean_daily_difference":float(d.mean()),
            "days_cheaper":int((d<-1e-9).sum()),"days_more_expensive":int((d>1e-9).sum()),
            "mean_daily_difference_95ci":[float(lo),float(hi)],"bootstrap":f"moving block, {block}-day blocks, {draws} draws, seed {seed}"}


def energy_evaluation(days=60):
    origin0=datetime(2026,1,1,tzinfo=timezone.utc);all_rows=records(examples.electricity(42+days))
    actual={t:(l,p) for t,k,l,p in all_rows};rows=[];last_month=None
    peaks={k:0. for k in POLICIES};states={k:(0. if k=="no_battery" else 40.) for k in POLICIES}
    for i in range(days):
        origin=origin0+timedelta(days=42+i);grid=[origin+timedelta(hours=h) for h in range(24)]
        if origin.month!=last_month:peaks={k:0. for k in POLICIES};last_month=origin.month
        f=forecast(all_rows,origin,grid,4);y=np.array([actual[t] for t in grid]);q=np.array(f["quantiles"])[:,0,:]
        row={"date":origin.date().isoformat(),"ridge_mae":float(np.mean(abs(np.array(f["point"])[:,0]-y[:,0]))),
             "naive_mae":float(np.mean(abs(np.array(f["seasonal_naive"])[:,0]-y[:,0]))),
             "coverage_80_load":float(np.mean((y[:,0]>=q[:,1])&(y[:,0]<=q[:,-2]))),**quantile_scores(f["levels"],q,y[:,0])}
        for label in POLICIES:
            p=policy_problem(label,f,y,states[label],peaks[label])
            plan=solve(p);cert=verify(p,plan);bill,peak=realized(plan,y,p)
            peaks[label]=max(peaks[label],peak);states[label]=plan["soc"][-1]
            row[label+"_cost"]=bill;row[label+"_status"]=cert["physical_status"]
        rows.append(row)
    return {"target_days":days,"origins":rows,"ridge_mae_kw":float(np.mean([r["ridge_mae"] for r in rows])),
            "seasonal_naive_mae_kw":float(np.mean([r["naive_mae"] for r in rows])),
            "candidate_total_cost":math.fsum(r["candidate_cost"] for r in rows),"seasonal_naive_total_cost":math.fsum(r["seasonal_naive_cost"] for r in rows),
            "verified_feasible_plans":sum(r[k+"_status"]=="VERIFIED_FEASIBLE" for r in rows for k in ("candidate","seasonal_naive")),
            "decision_quality":decision_summary(rows),
            "paired_comparisons":[paired_comparison(rows,a,"seasonal_naive") for a in ("candidate","candidate_risk_neutral")]
                                 +[paired_comparison(rows,"candidate","candidate_risk_neutral")],
            "decision_quality_note":("perfect_foresight is a hindsight reference that plans each day on the realized load under the same "
                                     "daily constraints and carried state; it is not a global bound across days. mean_worst_10pct_days is "
                                     "the empirical CVaR90 of realized daily cost, the quantity the risk weight trades expected cost for."),
            "scope":"Generated UTC site, all policies solved on the same battery and tariff, monthly peak and charge carried separately per policy."}


def environment():
    import platform,sqlite3,scipy
    return {"python":platform.python_version(),"numpy":np.__version__,"scipy":scipy.__version__,"sqlite":sqlite3.sqlite_version,
            "platform":platform.system()+"-"+platform.machine()}


def evaluate(out,days=60):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);result={};env=environment()
    for kind,fn in [("sanad",text_evaluation),("basira",vision_evaluation),("rasid",lambda:energy_evaluation(days))]:
        start=time.perf_counter();r=fn();r["elapsed_seconds"]=round(time.perf_counter()-start,4);r["fixture_only"]=True
        r["environment"]=env
        (out/(kind+".json")).write_bytes(canonical(r));result[kind]=r
    return result
