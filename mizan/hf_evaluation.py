"""Held-out evaluation harnesses with injectable components, for the pinned Hugging Face checkpoints.

Each harness repeats a shipped loop of mizan/evaluation.py operation for operation, with the component passed in.
Run with the current component, it must reproduce reports/evaluation/ (exactly; for RASID, the stated tolerances);
a gate checks that before any model result is trusted. This module never imports torch: callers pass feature functions, forecasters or embedders.
"""
import math
from collections.abc import Callable,Sequence
from datetime import datetime,timedelta,timezone
from fractions import Fraction
import numpy as np
from . import examples
from .basira import engine as b
from .core.contract import require
from .evaluation import POLICIES,decision_summary,paired_comparison,policy_problem,realized
from .rasid.forecast import HOUR,forecast,quantile_scores,records,vintage
from .rasid.solver import solve
from .rasid.verify import verify

_MISSING=object()


def _compare(pairs):
    """Per named value (name, got, want[, test]): exact equality, or the pair's own test when it carries one.
    A value absent on one side never matches, not even a null on the other."""
    checked=[p[0] for p in pairs]
    def matches(name,got,want,test=None):
        return got is not _MISSING and want is not _MISSING and (test(got,want) if test else got==want)
    mismatches=[p[0] for p in pairs if not matches(*p)]
    return {"passed":bool(checked) and not mismatches,"checked":checked,"mismatches":mismatches}


def _within(abs_tol=0.,rel_tol=0.):
    """A test for _compare: both are finite numbers and |got - want| <= max(abs_tol, rel_tol * |want|)."""
    def real(x):
        return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    return lambda got,want:real(got) and real(want) and abs(got-want)<=max(abs_tol,rel_tol*abs(want))


# ---------------------------------------------------------------------------------------------------------------
# BASIRA: the frozen 80-item test of vision_evaluation() and the repeated-calibration study of calibration_study()

BASIRA_ALPHA=Fraction(1,20)
BASIRA_DELTA=Fraction(1,10)
BASIRA_SEED=23
BASIRA_STUDY_SCOPE=("Generated rings only. Pools are finite samples, so each long-run rate is itself an estimate "
                    "(about ±0.35 percentage points at 5% for 4000 normals). Calibration sets are drawn without replacement from a 4000-part pool, which slightly understates bank-to-bank variance relative to fresh draws.")


def basira_hf(feature_fn,profile:str,*,draws=400,sizes=(24,50,100),pools=(4000,4000,400))->dict:
    """Run feature_fn through the shipped frozen test and repeated-calibration study.

    The bank and calibration scores come from b.fit(..., feature_fn, profile), and every scored image uses feature_fn;
    seeds, draws, ranks and float expressions are those of vision_evaluation() and calibration_study(). The pixel
    baseline of the frozen test is not repeated: it does not depend on the feature function."""
    p=examples.inspection();bank,cal,ident,_,_=b.fit(p["reference"],p["calibration"],feature_fn,profile)
    score=lambda im:b.score(feature_fn(im),bank)[0]
    rows=[]
    for i in range(80):
        defect=None if i<40 else ("pit" if i%2 else "crack");s=score(examples.part(9100+i,defect))
        pred=b.rank_decision(cal,s,".05")
        rows.append(dict(seed=9100+i,condition=defect or "intact",score=s,p_value=pred["p_value"],flag=pred["decision"]=="FLAG"))
    frozen={"test_items":80,"calibration_m":len(cal),"bank_digest":ident,
            "detected":sum(x["flag"] for x in rows[40:]),"false_alerts":sum(x["flag"] for x in rows[:40]),"rows":rows}
    height,width=bank.shape[1:3]
    return {"profile":profile,"frozen_test":frozen,"calibration_study":_basira_study(score,cal,ident,draws,sizes,pools),
            "patches_averaged":max(1,math.ceil(height*width*.025))}  # b.score averages its top 2.5% of patches


def _basira_study(score,shipped,ident,draws,sizes,pools,alpha=BASIRA_ALPHA,delta=BASIRA_DELTA,seed=BASIRA_SEED):
    """calibration_study() on an already fitted bank; each pool image is scored as soon as it is made, keeping scores only."""
    def scores(seeds,defect=None):
        return np.array([score(examples.part(s,defect(s) if defect else None)) for s in seeds])
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
            "minimum_m_for_pac":b.minimum_m(alpha,delta),"rows":rows,"scope":BASIRA_STUDY_SCOPE}


def _row_pairs(name,key,got_rows,want_rows,fields=None,tests=None):
    """Named pairs for two row lists matched by position (all fields of both sides unless given); a missing row is empty.
    tests maps a field to its _compare test; every other field is compared exactly."""
    pairs=[];tests=tests or {}
    for i in range(max(len(got_rows),len(want_rows))):
        g=got_rows[i] if i<len(got_rows) else {};w=want_rows[i] if i<len(want_rows) else {}
        label=f"{name}[{key}={w.get(key,g.get(key))}]."
        pairs+=[(label+k,g.get(k,_MISSING),w.get(k,_MISSING))+((tests[k],) if k in tests else ())
                for k in fields or sorted(set(g)|set(w))]
    return pairs


def basira_gate(result:dict,shipped:dict)->dict:
    """Exact-equality check of a harness run against reports/evaluation/basira.json (the shipped descriptor at full size).

    Checks the frozen test's detections, false alerts and bank digest, the seed, condition, p-value and flag of each
    frozen item, the study settings and bank digest, every field of the shipped-calibration summary and every field of
    every calibration row."""
    got,want=result.get("frozen_test",{}),shipped
    pairs=[(k,got.get(k,_MISSING),want.get(k,_MISSING)) for k in ("detected","false_alerts","bank_digest")]
    pairs+=_row_pairs("frozen_test.rows","seed",got.get("rows",[]),want["rows"],("seed","condition","p_value","flag"))
    got,want=result.get("calibration_study",{}),shipped["calibration_study"]
    pairs+=[("calibration_study."+k,got.get(k,_MISSING),want.get(k,_MISSING))
            for k in ("alpha","delta","draws_per_size","pools","minimum_m_for_pac","bank_digest")]
    g,w=got.get("shipped_calibration",{}),want["shipped_calibration"]
    pairs+=[("shipped_calibration."+k,g.get(k,_MISSING),w.get(k,_MISSING)) for k in sorted(set(g)|set(w))]
    pairs+=_row_pairs("rows","m",got.get("rows",[]),want["rows"])
    return _compare(pairs)


# ---------------------------------------------------------------------------------------------------------------
# RASID: the 60 rolling origins of energy_evaluation(), with a quantile forecaster planned next to the ridge policies

Forecaster=Callable[[np.ndarray,np.ndarray,Sequence[float]],np.ndarray]  # (load, pv, levels) -> (24, 2, len(levels))
CHRONOS_LEVELS=(.1,.3,.5,.7,.9)
SHARED_LEVELS=(.1,.5,.9)  # the levels both forecasters produce; Chronos-Bolt is trained on 0.1 to 0.9 only
POLICIES_HF=POLICIES+("chronos","chronos_risk_neutral")
PLANNED_AS={"chronos":"candidate","chronos_risk_neutral":"candidate_risk_neutral"}
RASID_COMPARISONS=(("chronos_risk_neutral","candidate_risk_neutral"),("chronos_risk_neutral","seasonal_naive"),
                   ("chronos","candidate"))
RASID_MAE_TOLERANCE=1e-9         # kW, absolute: ridge and seasonal-naive errors
RASID_CANDIDATE_TOLERANCE=5e-4   # relative: candidate and candidate_risk_neutral totals (MILP solver version)
RASID_BASELINE_TOLERANCE=1e-6    # relative: the other shipped policies' totals
SCENARIO_RULE=("Five equally weighted scenarios (weight 0.2 each) from the forecaster's quantiles at levels "
               "0.1, 0.3, 0.5, 0.7 and 0.9, numbered j = 0 to 4: scenario j pairs load quantile path j with solar "
               "quantile path 4 − j, so high load meets low solar. Before pairing, each hour's quantiles are sorted "
               "across levels (monotone rearrangement) and solar values below zero are clipped at zero (counted in "
               "pv_values_clipped). Load quantiles are not clipped: a negative load value stops the run at the "
               "planner's input check. The chronos and chronos_risk_neutral policies are the candidate (risk weight 0.35) "
               "and candidate_risk_neutral problems with only these weights and scenarios replaced; the ridge policies "
               "keep their four reduced residual scenarios as shipped.")
FORECAST_NOTE=("Accuracy on the load series, as in the shipped evaluation. MAE of the ridge point forecast, the "
               "seasonal-naive path and the Chronos median (0.5). Pinball loss and truncated CRPS from quantile_scores "
               "on the shared levels 0.1, 0.5 and 0.9 for both forecasters; 80% coverage between 0.1 and 0.9. "
               "The forecaster sees only the contiguous run of hours known at the origin, and that run must end at the "
               "hour before the origin, so that its 24 steps are the planned day; otherwise the run stops (CONTEXT).")


def chronos_context(rows,origin)->tuple[np.ndarray,np.ndarray,int,datetime]:
    """Load and solar values (oldest first) of the contiguous run of hours ending at the latest hour known at origin,
    under the vintage rule of rasid.forecast, with their count and that latest hour. Nothing at or after origin and
    nothing known after it is ever returned; a missing hour ends the run."""
    hist=vintage(rows,origin);require(hist,"CONTEXT","No history is known at the origin")
    last=max(hist);first=last
    while first-HOUR in hist:first-=HOUR
    hours=[first+i*HOUR for i in range((last-first)//HOUR+1)]
    return np.array([hist[t][0] for t in hours]),np.array([hist[t][1] for t in hours]),len(hours),last


def _columns(levels,wanted):
    levels=[float(x) for x in levels]
    require(all(x in levels for x in wanted),"LEVELS","A shared scoring level is missing")
    return [levels.index(x) for x in wanted]


def _decision_summary(rows,policies=POLICIES_HF):
    """evaluation.decision_summary() for each policy. The shipped function covers evaluation.POLICIES only, so a
    Chronos policy is summarised by it in the place of the policy it is planned as, with its own costs and statuses."""
    shipped=decision_summary(rows)
    def summary(k):
        if k not in PLANNED_AS:return shipped[k]
        a=PLANNED_AS[k]
        return decision_summary([{**r,a+"_cost":r[k+"_cost"],a+"_status":r[k+"_status"]} for r in rows])[a]
    return {k:summary(k) for k in policies}


def rasid_hf(forecaster:Forecaster,*,days=60)->dict:
    """energy_evaluation() operation for operation, with two more policies planned on the forecaster's quantiles.

    Per origin: the shipped ridge forecast, errors and five policies; then the forecaster sees only chronos_context()
    (the vintage at the origin) and its quantiles at CHRONOS_LEVELS are sorted across levels and solar is clipped at
    zero before scoring and planning (SCENARIO_RULE). Each of the seven policies carries its own battery state and
    month-to-date peak. Ridge and the forecaster are both scored on SHARED_LEVELS."""
    origin0=datetime(2026,1,1,tzinfo=timezone.utc);all_rows=records(examples.electricity(42+days))
    actual={t:(l,p) for t,k,l,p in all_rows};rows=[];last_month=None
    peaks={k:0. for k in POLICIES_HF};states={k:(0. if k=="no_battery" else 40.) for k in POLICIES_HF}
    lo,mid,hi=_columns(CHRONOS_LEVELS,SHARED_LEVELS);top=len(CHRONOS_LEVELS)-1
    for i in range(days):
        origin=origin0+timedelta(days=42+i);grid=[origin+timedelta(hours=h) for h in range(24)]
        if origin.month!=last_month:peaks={k:0. for k in POLICIES_HF};last_month=origin.month
        f=forecast(all_rows,origin,grid,4);y=np.array([actual[t] for t in grid]);q=np.array(f["quantiles"])[:,0,:]
        row={"date":origin.date().isoformat(),"ridge_mae":float(np.mean(abs(np.array(f["point"])[:,0]-y[:,0]))),
             "naive_mae":float(np.mean(abs(np.array(f["seasonal_naive"])[:,0]-y[:,0]))),
             "coverage_80_load":float(np.mean((y[:,0]>=q[:,1])&(y[:,0]<=q[:,-2])))}
        ridge=quantile_scores(SHARED_LEVELS,q[:,_columns(f["levels"],SHARED_LEVELS)],y[:,0])
        load,pv,n,last=chronos_context(all_rows,origin)
        require(last==origin-HOUR,"CONTEXT","The latest known hour must be the hour before the origin")
        c=np.asarray(forecaster(load,pv,CHRONOS_LEVELS),dtype=np.float64)
        require(c.shape==(24,2,len(CHRONOS_LEVELS)) and np.all(np.isfinite(c)),"FORECAST","Expected finite (24, 2, levels) quantiles")
        c=np.sort(c,axis=-1);clipped=int(np.sum(c[:,1,:]<0));c[:,1,:]=np.maximum(c[:,1,:],0)
        cl=c[:,0,:];chronos=quantile_scores(SHARED_LEVELS,cl[:,[lo,mid,hi]],y[:,0])
        row.update(ridge_mean_pinball=ridge["mean_pinball"],ridge_truncated_crps=ridge["truncated_crps"],
                   chronos_mae=float(np.mean(abs(cl[:,mid]-y[:,0]))),
                   chronos_coverage_80_load=float(np.mean((y[:,0]>=cl[:,lo])&(y[:,0]<=cl[:,hi]))),
                   chronos_mean_pinball=chronos["mean_pinball"],chronos_truncated_crps=chronos["truncated_crps"],
                   context_hours=n,pv_values_clipped=clipped)
        fc={**f,"weights":[1/len(CHRONOS_LEVELS)]*len(CHRONOS_LEVELS),
            "scenarios":[np.stack([c[:,0,j],c[:,1,top-j]],axis=1).tolist() for j in range(top+1)]}
        for label in POLICIES_HF:
            p=policy_problem(PLANNED_AS.get(label,label),fc if label in PLANNED_AS else f,y,states[label],peaks[label])
            plan=solve(p);cert=verify(p,plan);bill,peak=realized(plan,y,p)
            peaks[label]=max(peaks[label],peak);states[label]=plan["soc"][-1]
            row[label+"_cost"]=bill;row[label+"_status"]=cert["physical_status"]
        rows.append(row)
    mean=lambda k:float(np.mean([r[k] for r in rows]))
    return {"target_days":days,"origins":rows,
            "forecast":{"ridge_mae_kw":mean("ridge_mae"),"seasonal_naive_mae_kw":mean("naive_mae"),"chronos_mae_kw":mean("chronos_mae"),
                        "shared_levels":list(SHARED_LEVELS),"ridge_mean_pinball":mean("ridge_mean_pinball"),
                        "ridge_truncated_crps":mean("ridge_truncated_crps"),"ridge_coverage_80_load":mean("coverage_80_load"),
                        "chronos_mean_pinball":mean("chronos_mean_pinball"),"chronos_truncated_crps":mean("chronos_truncated_crps"),
                        "chronos_coverage_80_load":mean("chronos_coverage_80_load"),
                        "pv_values_clipped":sum(r["pv_values_clipped"] for r in rows),"note":FORECAST_NOTE},
            "decision_quality":_decision_summary(rows,POLICIES_HF),
            "paired_comparisons":[paired_comparison(rows,a,ref) for a,ref in RASID_COMPARISONS],
            "scenario_rule":SCENARIO_RULE}


def rasid_gate(result:dict,shipped:dict)->dict:
    """Check a harness run's ridge side against reports/evaluation/rasid.json (the shipped energy_evaluation()).

    Checks the day count; the mean ridge and seasonal-naive errors and each origin's date, errors and ridge 80%
    coverage; and each shipped policy's total cost and verified-plan count. Errors are compared within
    RASID_MAE_TOLERANCE, the candidate and candidate_risk_neutral totals within RASID_CANDIDATE_TOLERANCE (the
    shipped file was solved with another SciPy), the other totals within RASID_BASELINE_TOLERANCE; the rest exactly."""
    mae=_within(abs_tol=RASID_MAE_TOLERANCE)
    pairs=[("target_days",result.get("target_days",_MISSING),shipped.get("target_days",_MISSING))]
    got=result.get("forecast",{})
    pairs+=[("forecast."+k,got.get(k,_MISSING),shipped.get(k,_MISSING),mae) for k in ("ridge_mae_kw","seasonal_naive_mae_kw")]
    got=result.get("decision_quality",{})
    for k in POLICIES:
        g,w=got.get(k,{}),shipped["decision_quality"][k]
        cost=_within(rel_tol=RASID_CANDIDATE_TOLERANCE if k in ("candidate","candidate_risk_neutral") else RASID_BASELINE_TOLERANCE)
        pairs+=[(f"decision_quality.{k}.total_cost",g.get("total_cost",_MISSING),w.get("total_cost",_MISSING),cost),
                (f"decision_quality.{k}.verified_feasible_plans",g.get("verified_feasible_plans",_MISSING),
                 w.get("verified_feasible_plans",_MISSING))]
    pairs+=_row_pairs("origins","date",result.get("origins",[]),shipped["origins"],
                      ("date","ridge_mae","naive_mae","coverage_80_load"),{"ridge_mae":mae,"naive_mae":mae})
    return _compare(pairs)
