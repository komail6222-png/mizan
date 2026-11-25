"""Held-out evaluation harnesses with injectable components, for the pinned Hugging Face checkpoints.

The BASIRA and RASID harnesses repeat a shipped loop of mizan/evaluation.py operation for operation, with the
component passed in. Run with the current component, they must reproduce reports/evaluation/ (exactly; for RASID,
the stated tolerances); a gate checks that before any model result is trusted. SANAD has no shipped retrieval
numbers: its harness ranks a generated retrieval set whose gold comes from the grammar, and its gate checks that two
runs agree exactly on the set and on the engine's own lexical retriever. This module never imports torch: callers
pass feature functions, forecasters or embedders.
"""
import math
import random
from collections.abc import Callable,Sequence
from datetime import datetime,timedelta,timezone
from fractions import Fraction
import numpy as np
from . import examples
from .basira import engine as b
from .core.contract import digest,require
from .evaluation import POLICIES,decision_summary,paired_comparison,policy_problem,realized
from .rasid.forecast import HOUR,forecast,quantile_scores,records,vintage
from .rasid.solver import solve
from .rasid.verify import verify
from .sanad import engine as s

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
    (the vintage at the origin) and its quantiles at CHRONOS_LEVELS are sorted across levels before scoring and
    planning; solar is clipped at zero before planning (SCENARIO_RULE) and is never scored. Each of the seven policies
    carries its own battery state and month-to-date peak. Ridge and the forecaster are both scored on SHARED_LEVELS,
    on the load series only."""
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


# ---------------------------------------------------------------------------------------------------------------
# SANAD: a generated retrieval set whose gold is the grammar's claims, ranked by the engine's retriever and an embedder

Embedder=Callable[[list[str]],np.ndarray]  # strings -> (n, d) vectors; the harness adds the "query: " / "passage: " prefixes
DISTRACTORS = {"en": ["Input voltage: 230 V", "Weight: 32 kg", "Efficiency (eco mode): 99 %", "Derated 10% above 40 °C",
                      "Rated apparent power varies by configuration", "Dimensions: 440 x 600 mm", "Noise level: 45 dB", "Battery type: VRLA"],
               "ar": ["جهد الدخل: 230 فولت", "الوزن: ٣٢ كجم", "الكفاءة في الوضع الاقتصادي: ٩٩٪", "ينخفض الأداء ١٠٪ فوق ٤٠ درجة",
                      "القدرة الظاهرية الاسمية تختلف حسب التهيئة", "الأبعاد: ٤٤٠ × ٦٠٠ مم", "مستوى الضجيج: ٤٥ ديسيبل", "نوع البطارية: VRLA"]}
# attribute: (lowest and highest whole value, English unit spellings, Arabic unit spellings); every spelling is in s.UNITS
RETRIEVAL_VALUES={"apparent_power":(10,200,("kVA",),("kVA","كيلو فولت أمبير")),"active_power":(8,180,("kW",),("kW","كيلوواط")),
                  "runtime_100":(5,30,("min","minutes"),("دقيقة","دقائق")),"efficiency_100":(90,97,("%",),("٪",)),
                  "transfer_time":(2,12,("ms",),("ms","مللي ثانية"))}
MODEL_FAMILIES=("Atlas","Noor","Sahm","Qamar","Rawda","Waha")
HEADINGS={"en":"Model: ","ar":"الطراز: "}
QUERY_KINDS=("bilingual","en","ar")
RETRIEVAL_LANGUAGES=("en","ar","all")
RETRIEVAL_METRICS=("recall_at_1","recall_at_3","mrr_at_4")
LEXICAL_RANKER="mizan.sanad.engine.retrieve"
ARABIC_DIGITS=str.maketrans("0123456789","٠١٢٣٤٥٦٧٨٩")
SANAD_PROTOCOL=("Generated retrieval set: seeded single-page datasheets, alternately English and Arabic, each a model heading, "
                "then three to five scored attribute lines (each attribute at most once, grammar-valid values, Arabic-Indic digits "
                "in Arabic) and four to six near-miss lines from a fixed distractor pool per language, shuffled together. Gold is "
                "the line sanad.engine.parse_document extracts as the claim for each attribute; the generator stops "
                "(RETRIEVAL_SET) unless every attribute line is that claim and no other line is any claim. Each attribute is "
                "asked three ways: bilingual (English and Arabic labels joined, as assess builds its query), English label only "
                "and Arabic label only. Both retrievers rank every line of the document, the heading included. Lexical: "
                "sanad.engine.retrieve, unchanged (character-trigram TF-IDF), which returns its top four. Embedding: cosine "
                "between 'query: ' and 'passage: ' vectors, each document's lines encoded in one call and all queries in one "
                "call; ties keep document order, as the stable sort in retrieve does. Recall@1, recall@3 and MRR@4 "
                "(reciprocal rank 0 beyond rank four, where the engine stops). Verdicts are not recomputed from any ranking: "
                "the grammar keeps verdict authority.")


def _attribute_line(attr,lang,rng):
    low,high,english,arabic=RETRIEVAL_VALUES[attr]
    value=str(rng.randint(low,high));unit=rng.choice(english if lang=="en" else arabic)
    if lang=="ar":value=value.translate(ARABIC_DIGITS)
    return s.ATTR[attr][0 if lang=="en" else 1]+": "+value+("" if unit in ("%","٪") else " ")+unit


def retrieval_set(n_docs=120,seed=2024)->list[dict]:
    """Seeded single-page datasheets, alternately English and Arabic: a model heading, then three to five scored
    attribute lines (each attribute at most once) and four to six lines of the language's distractor pool, shuffled.

    gold maps each attribute to the index in lines of the line s.parse_document extracts as its claim. The set stops
    (RETRIEVAL_SET) unless the grammar's claims are exactly the generated attribute lines, one per attribute: every
    attribute line is extracted as that attribute's claim and no distractor (nor the heading) is any claim."""
    require(type(n_docs) is int and n_docs>=2,"RETRIEVAL_SET","Expected at least two documents, one per language")
    rng=random.Random(seed);items=[]
    for i in range(n_docs):
        lang=("en","ar")[i%2];model=f"{rng.choice(MODEL_FAMILIES)}-{rng.randint(10,400)}"
        body=[(a,_attribute_line(a,lang,rng)) for a in rng.sample(list(s.ATTR),rng.randint(3,5))]
        body+=[(None,x) for x in rng.sample(DISTRACTORS[lang],rng.randint(4,6))]
        rng.shuffle(body)
        lines=[HEADINGS[lang]+model]+[x for _,x in body];page="\n".join(lines)
        doc={"name":f"retrieval-{i:03d}-{lang}","pages":[page]}
        found=list(s.page_lines(page));index={start:j for j,(start,_) in enumerate(found)}
        require([x for _,x in found]==lines and len(set(lines))==len(lines),"RETRIEVAL_SET",f"Document {i}: lines are not distinct")
        gold={}
        for c in s.parse_document(doc,model)["claims"]:
            gold.setdefault(c["attribute"],[]).append(index.get(c["start"]) if c["page"]==1 else None)
        want={a:[j] for j,(a,_) in enumerate(body,1) if a}
        require(gold==want,"RETRIEVAL_SET",f"Document {i}: the grammar's claims {gold} are not the attribute lines {want}")
        items.append({"doc":doc,"model":model,"language":lang,"gold":gold,"lines":lines})
    return items


def retrieval_set_digest(items)->str:
    """core.contract.digest of the documents; the model, language, lines and gold follow from them through the grammar."""
    return digest([it["doc"] for it in items])


def _query(attr,kind):
    english,arabic=s.ATTR[attr]
    return {"bilingual":" ".join(s.ATTR[attr]),"en":english,"ar":arabic}[kind]  # bilingual: the query assess builds


def _unit_rows(embed_fn,strings):
    v=np.asarray(embed_fn(strings),dtype=np.float64)
    require(v.ndim==2 and v.shape[0]==len(strings) and v.shape[1]>0 and bool(np.all(np.isfinite(v))),
            "EMBEDDING","Expected finite (n, d) vectors, one row per string")
    norm=np.linalg.norm(v,axis=1,keepdims=True);require(bool(np.all(norm>0)),"EMBEDDING","A zero vector has no direction")
    return v/norm


def _rank(order,gold):
    """1-based rank of the first gold line among the first four of order, or None beyond four."""
    return next((k for k,j in enumerate(order[:4],1) if j in gold),None)


def _retrieval_metrics(ranks):
    n=len(ranks)
    return {"recall_at_1":sum(r==1 for r in ranks)/n,"recall_at_3":sum(r is not None and r<=3 for r in ranks)/n,
            "mrr_at_4":sum(1/r for r in ranks if r)/n}


def _qualified(fn):
    return f"{getattr(fn,'__module__',None)}.{getattr(fn,'__qualname__',type(fn).__name__)}"


def sanad_hf(embed_fn:Embedder,*,n_docs=120,seed=2024)->dict:
    """Rank every line of each retrieval_set() document for each (attribute, query kind) with the engine's own
    s.retrieve (its top four) and with cosine over embed_fn vectors, and score both against the grammar's gold.

    embed_fn is called once with every query ("query: " prefix) and then once per document with all its lines
    ("passage: " prefix). metrics[retriever][query_kind][doc_language] holds recall@1, recall@3 and MRR@4; queries
    counts (document, attribute) pairs, each asked in the three query kinds. lexical_ranker names the function that
    ranked the lexical side; nothing about verdicts is computed here (SANAD_PROTOCOL)."""
    items=retrieval_set(n_docs,seed);rank=s.retrieve
    asked=[(a,kind) for a in s.ATTR for kind in QUERY_KINDS];column={x:i for i,x in enumerate(asked)}
    queries=_unit_rows(embed_fn,["query: "+_query(*x) for x in asked])
    ranks={(r,kind,lang):[] for r in ("lexical","e5") for kind in QUERY_KINDS for lang in RETRIEVAL_LANGUAGES}
    for it in items:
        lines=it["lines"];where={x:j for j,x in enumerate(lines)};rows=[{"quote":x,"page":1,"source":""} for x in lines]
        passages=_unit_rows(embed_fn,["passage: "+x for x in lines])
        require(passages.shape[1]==queries.shape[1],"EMBEDDING","Query and passage vectors differ in width")
        cosine=passages@queries.T
        for a,gold in it["gold"].items():
            for kind in QUERY_KINDS:
                lexical=[where[x["quote"]] for x in rank(rows,_query(a,kind))]
                e5=np.argsort(-cosine[:,column[a,kind]],kind="stable").tolist()
                for name,order in (("lexical",lexical),("e5",e5)):
                    for lang in (it["language"],"all"):ranks[name,kind,lang].append(_rank(order,gold))
    count=lambda lang:sum(len(it["gold"]) for it in items if lang in (it["language"],"all"))
    return {"n_docs":n_docs,"seed":seed,"set_digest":retrieval_set_digest(items),
            "documents":{lang:sum(it["language"]==lang for it in items) for lang in ("en","ar")},
            "queries":count("all"),"queries_by_language":{lang:count(lang) for lang in ("en","ar")},"lexical_ranker":_qualified(rank),
            "metrics":{name:{kind:{lang:_retrieval_metrics(ranks[name,kind,lang]) for lang in RETRIEVAL_LANGUAGES}
                             for kind in QUERY_KINDS} for name in ("lexical","e5")},
            "protocol":SANAD_PROTOCOL}


def sanad_gate(first:dict,second:dict)->dict:
    """Exact agreement of two sanad_hf runs on everything the embedder cannot change.

    Checks set_digest and queries; lexical_ranker, which must be LEXICAL_RANKER (the engine's s.retrieve) in both;
    and every lexical metric (3 query kinds x 3 languages x 3 metrics). The embedding side is not compared."""
    def lexical(r,kind,lang,m):
        return r.get("metrics",{}).get("lexical",{}).get(kind,{}).get(lang,{}).get(m,_MISSING)
    pairs=[(k,first.get(k,_MISSING),second.get(k,_MISSING)) for k in ("set_digest","queries")]
    pairs.append(("lexical_ranker",first.get("lexical_ranker",_MISSING),second.get("lexical_ranker",_MISSING),
                  lambda got,want:got==want==LEXICAL_RANKER))
    pairs+=[(f"metrics.lexical.{kind}.{lang}.{m}",lexical(first,kind,lang,m),lexical(second,kind,lang,m))
            for kind in QUERY_KINDS for lang in RETRIEVAL_LANGUAGES for m in RETRIEVAL_METRICS]
    return _compare(pairs)
