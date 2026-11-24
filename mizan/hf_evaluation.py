"""Held-out evaluation harnesses with injectable components, for the pinned Hugging Face checkpoints.

Each harness repeats a shipped loop of mizan/evaluation.py operation for operation, with the component passed in.
Run with the current component, it must reproduce reports/evaluation/ exactly; a gate checks that before any model
result is trusted. This module never imports torch: callers pass feature functions, forecasters or embedders.
"""
import math
from fractions import Fraction
import numpy as np
from . import examples
from .basira import engine as b

_MISSING=object()


def _compare(pairs):
    """Exact equality per named value; a value absent on one side never matches, not even a null on the other."""
    checked=[name for name,_,_ in pairs]
    mismatches=[name for name,got,want in pairs if got is _MISSING or want is _MISSING or not got==want]
    return {"passed":bool(checked) and not mismatches,"checked":checked,"mismatches":mismatches}


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


def basira_gate(result:dict,shipped:dict)->dict:
    """Exact-equality check of a harness run against reports/evaluation/basira.json (the shipped descriptor at full size).

    Checks the frozen test's detections, false alerts and bank digest, the study settings and bank digest, every field
    of the shipped-calibration summary and every field of every calibration row."""
    got,want=result.get("frozen_test",{}),shipped
    pairs=[(k,got.get(k,_MISSING),want.get(k,_MISSING)) for k in ("detected","false_alerts","bank_digest")]
    got,want=result.get("calibration_study",{}),shipped["calibration_study"]
    pairs+=[("calibration_study."+k,got.get(k,_MISSING),want.get(k,_MISSING))
            for k in ("alpha","delta","draws_per_size","pools","minimum_m_for_pac","bank_digest")]
    g,w=got.get("shipped_calibration",{}),want["shipped_calibration"]
    pairs+=[("shipped_calibration."+k,g.get(k,_MISSING),w.get(k,_MISSING)) for k in sorted(set(g)|set(w))]
    got_rows,want_rows=got.get("rows",[]),want["rows"]
    for i in range(max(len(got_rows),len(want_rows))):
        g=got_rows[i] if i<len(got_rows) else {};w=want_rows[i] if i<len(want_rows) else {}
        label=f"rows[m={w.get('m',g.get('m'))}]."
        pairs+=[(label+k,g.get(k,_MISSING),w.get(k,_MISSING)) for k in sorted(set(g)|set(w))]
    return _compare(pairs)
