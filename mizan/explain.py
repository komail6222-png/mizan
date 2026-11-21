"""Proof-rendered explanations and executed counterfactuals.

Explanations are rendered only from fields of a verified result: every sentence points at evidence the result
already carries (document, page, character span, score, certificate). Nothing is generated, so nothing can be
invented. Counterfactuals answer "what would change this decision?" and each one is *executed*: the changed input
goes back through the same engine (SANAD assessment, BASIRA decision rule, RASID solver plus independent verifier)
and the new outcome is reported with how it was established. Changes that only an operator may make, such as
approving a document, are labelled operator-only; they are hypotheses an agent can surface, never actions it takes.
"""
import copy
import math
from fractions import Fraction

VERDICT_AR = {"MEETS": "مستوفٍ", "DOES_NOT_MEET": "غير مستوفٍ", "REVIEW": "يحتاج مراجعة", "FLAG": "إنذار: يُحال للفحص"}
OPS = {"ge": "≥", "le": "≤"}


def number(value):
    q = Fraction(str(value))
    return str(q.numerator) if q.denominator == 1 else f"{float(q):.4g}"


def where(c):
    return f"{c['document']} p.{c['page']} chars {c['start']}–{c['end']}"


def where_ar(c):
    return f"{c['document']} ص{c['page']} الأحرف {c['start']}–{c['end']}"


# ---------------------------------------------------------------- SANAD ----
def sanad_sentences(req):
    from .sanad.engine import ATTR
    rule, verdict, reason = req["rule"], req["verdict"], req["reason"]
    en_attr, ar_attr = ATTR[rule["attribute"]]
    head_en = f"{en_attr} {OPS[rule['op']]} {rule['threshold']} {rule['unit']}: {verdict}"
    head_ar = f"{ar_attr} {OPS[rule['op']]} {rule['threshold']} {rule['unit']}: {VERDICT_AR[verdict]}"
    ev = req["evidence"]
    cites_en = "; ".join(f"“{c['quote']}” ({where(c)})" for c in ev[:4])
    cites_ar = "؛ ".join(f"«{c['quote']}» ({where_ar(c)})" for c in ev[:4])
    if reason == "SUPPORTED_INTERVAL":
        en = f"{head_en}. Approved evidence: {cites_en}."
        ar = f"{head_ar}. الدليل المعتمد: {cites_ar}."
    elif reason == "SOURCE_CONFLICT":
        en = f"{head_en}. Approved sources disagree: {cites_en}."
        ar = f"{head_ar}. المصادر المعتمدة متعارضة: {cites_ar}."
    elif reason == "INCOMPLETE_CONTEXT":
        gaps = req["coverage_gaps"]
        shown_en = "; ".join(f"“{g['quote']}” ({where(g)}, {g['reason']})" for g in gaps[:3])
        shown_ar = "؛ ".join(f"«{g['quote']}» ({where_ar(g)})" for g in gaps[:3])
        more = f" and {len(gaps) - 3} more" if len(gaps) > 3 else ""
        en = f"{head_en}. {len(gaps)} line(s) in approved documents fall outside the grammar and may qualify the value: {shown_en}{more}."
        ar = f"{head_ar}. يوجد {len(gaps)} سطر في المستندات المعتمدة خارج القواعد وقد يغيّر معنى القيمة: {shown_ar}."
    elif reason == "BOUND_IS_INCONCLUSIVE":
        en = f"{head_en}. The stated bound cannot establish the requirement: {cites_en}."
        ar = f"{head_ar}. الحد المذكور لا يكفي لإثبات الشرط: {cites_ar}."
    else:
        if ev:
            en = f"{head_en}. A qualifier makes the value unknown: {cites_en}."
            ar = f"{head_ar}. وصفٌ مثل «اختياري» يجعل القيمة غير معروفة: {cites_ar}."
        else:
            en = f"{head_en}. No approved document states this attribute for the offered model."
            ar = f"{head_ar}. لا يوجد مستند معتمد يذكر هذه الخاصية للطراز المعروض."
    if req.get("acknowledged_context"):
        n = len(req["acknowledged_context"])
        en += f" {n} line(s) were acknowledged as non-evidential by the approving operator."
        ar += f" اعتمد المراجِع {n} سطر على أنه ليس دليلًا."
    return en, ar


def sanad_counterfactuals(payload, profile, result):
    from .sanad import engine as s
    prepared = s.prepare(payload)
    mode = result["mode"]
    approvals = profile.get("approved_sources", {})
    out = []

    def rerun(rules, levels=None, policy=None):
        got = s.assess(prepared, rules, {**profile, "approved_sources": levels if levels is not None else approvals}, policy or mode)
        return got["requirements"]

    for req in result["requirements"]:
        if req["verdict"] == "MEETS":
            continue
        rule = req["rule"]
        found = []
        other = "highest_authority" if mode == "all_sources" else "all_sources"
        alt = rerun([rule], policy=other)[0]
        if alt["verdict"] != req["verdict"]:
            found.append({"change": f"Evaluate under the {other} policy", "change_ar": "تقييم بسياسة " + ("أعلى مصدر اعتمادًا" if other == "highest_authority" else "كل المصادر"),
                          "verdict": alt["verdict"], "established_by": "re-executed SANAD assessment", "actor": "operator policy"})
        if req["reason"] == "INCOMPLETE_CONTEXT":
            eligible = [g for g in req["coverage_gaps"] if g["reason"] == "UNCOVERED_CONTEXT" and s.acknowledgeable(g["quote"])]
            blocked = [g for g in req["coverage_gaps"] if g not in eligible]
            if eligible and not blocked:
                levels = copy.deepcopy(approvals)
                for g in eligible:
                    entry, level = levels[g["source"]], s.approval(levels[g["source"]])[0]
                    existing = entry.get("acknowledged", []) if isinstance(entry, dict) else []
                    levels[g["source"]] = {"level": level, "acknowledged": existing + [
                        {"page": g["page"], "start": g["start"], "end": g["end"], "quote_sha256": s.quote_digest(g["quote"])}]}
                new = rerun([rule], levels)[0]
                found.append({"change": f"Acknowledge {len(eligible)} non-evidential line(s): " + "; ".join(f"“{g['quote']}”" for g in eligible[:3]),
                              "change_ar": f"اعتماد {len(eligible)} سطر على أنه ليس دليلًا", "verdict": new["verdict"],
                              "established_by": "re-executed SANAD assessment with a hypothetical signed approval", "actor": "operator only"})
            for g in blocked[:3]:
                found.append({"change": f"Correct the transcript of “{g['quote']}” ({where(g)})", "change_ar": f"تصحيح نص السطر «{g['quote']}»",
                              "verdict": None, "established_by": "not executable: the line names a scored attribute or a measurable value",
                              "actor": "operator only"})
        if req["verdict"] == "DOES_NOT_MEET":
            ev = req["evidence"]
            bound = (min(Fraction(c["value"]) for c in ev if c["qualifier"] in ("exact", "upper")) if rule["op"] == "ge"
                     else max(Fraction(c["value"]) for c in ev if c["qualifier"] in ("exact", "lower")))
            text = f"{float(bound):.5f}".rstrip("0").rstrip(".") if bound.denominator != 1 else str(bound.numerator)
            if Fraction(text) == bound and s.THRESHOLD.fullmatch(text):
                new = rerun([{**rule, "threshold": text}])[0]
                found.append({"change": f"Relax the requirement to {OPS[rule['op']]} {text} {rule['unit']}",
                              "change_ar": f"تخفيف الشرط إلى {OPS[rule['op']]} {text} {rule['unit']}",
                              "verdict": new["verdict"], "established_by": "re-executed SANAD assessment", "actor": "tender owner"})
        if not req["evidence"] and result.get("unapproved_sources"):
            pending = [(meta["name"], d) for meta, d in zip(payload["documents"], prepared["documents"]) if d["source"] not in approvals]
            for name, d in pending[:3]:
                levels = {**approvals, d["source"]: 1}
                new = rerun([rule], levels)[0]
                change, change_ar = f"An operator approves “{name}” at level 1", f"اعتماد المستند «{name}» من قبل المشغّل"
                if new["verdict"] == req["verdict"] and new["reason"] == "INCOMPLETE_CONTEXT":
                    lines = [g for g in new["coverage_gaps"] if g["source"] == d["source"]]
                    eligible = [g for g in lines if g["reason"] == "UNCOVERED_CONTEXT" and s.acknowledgeable(g["quote"])]
                    if eligible and len(eligible) == len(new["coverage_gaps"]):
                        levels[d["source"]] = {"level": 1, "acknowledged": [
                            {"page": g["page"], "start": g["start"], "end": g["end"], "quote_sha256": s.quote_digest(g["quote"])} for g in eligible]}
                        new = rerun([rule], levels)[0]
                        # Name the exact text the operator would be declaring non-evidential: injected text stays visible.
                        change += " and declares these lines non-evidential: " + "; ".join(f"“{g['quote']}”" for g in eligible[:3])
                        change_ar += f" واعتماد {len(eligible)} سطر على أنه ليس دليلًا"
                if new["verdict"] != req["verdict"]:
                    found.append({"change": change, "change_ar": change_ar, "verdict": new["verdict"],
                                  "established_by": "re-executed SANAD assessment with a hypothetical signed approval", "actor": "operator only"})
        out.append({"rule": rule, "current": req["verdict"], "counterfactuals": found})
    return out


# --------------------------------------------------------------- BASIRA ----
def basira_sentences(r):
    a = float(Fraction(r["alpha"]))
    tc = r.get("training_conditional", {})
    m = r["calibration_count"]
    base_en = (f"Inspection {r['decision']}: anomaly score {r['score']:.3f} on part group “{r['group']}”, "
               f"conformal p-value {r['p_numerator']}/{r['p_denominator']} = {r['p_value']:.3f} against α = {a:g} "
               f"with {m} calibration parts.")
    base_ar = (f"نتيجة الفحص: {VERDICT_AR.get(r['decision'], r['decision'])}. درجة الشذوذ {r['score']:.3f} للقطعة «{r['group']}»، "
               f"وقيمة p المطابقة {r['p_numerator']}/{r['p_denominator']} = {r['p_value']:.3f} مقابل α = {a:g} مع {m} قطعة معايرة.")
    if r["mode"].startswith("ADVISORY"):
        base_en += f" With {m} parts the smallest attainable p-value is 1/{m + 1}, so this bank cannot raise an alert at α."
        base_ar += f" مع {m} قطع أصغر قيمة p ممكنة هي 1/{m + 1}، لذلك لا يقدر هذا البنك يطلق إنذارًا عند α."
    elif r.get("threshold") is not None:
        base_en += f" Threshold {r['threshold']:.3f} (rank {r['k']} of {m})."
        base_ar += f" العتبة {r['threshold']:.3f} (الترتيب {r['k']} من {m})."
    if tc.get("marginal_rank_exceedance") is not None:
        base_en += (f" Probability that this bank's long-run false-alert rate exceeds α: {100 * tc['marginal_rank_exceedance']:.1f}%.")
        base_ar += f" احتمال أن تتجاوز نسبة الإنذار الكاذب لهذا البنك على المدى الطويل α: {100 * tc['marginal_rank_exceedance']:.1f}%."
    base_en += " Automatic PASS is disabled: a REVIEW is not a clearance."
    base_ar += " القبول التلقائي معطّل: «مراجعة» لا تعني أن القطعة سليمة."
    return base_en, base_ar


def basira_counterfactuals(r):
    from .basira import engine as b
    scores, a = r["calibration_scores"], r["alpha"]
    out = []
    if r.get("threshold") is not None and r.get("guarantee", "marginal") == "marginal":
        t = r["threshold"]
        above = math.nextafter(t, math.inf)
        if r["decision"] == "REVIEW":
            check = b.rank_decision(scores, above, a)["decision"]
            out.append({"change": f"Anomaly score above {t:.4f} (now {r['score']:.4f})", "change_ar": f"درجة شذوذ أعلى من {t:.4f}",
                        "verdict": check, "established_by": "re-executed decision rule on the same calibration scores", "actor": "the part itself"})
        else:
            check = b.rank_decision(scores, t, a)["decision"]
            out.append({"change": f"Anomaly score at or below {t:.4f} (now {r['score']:.4f})", "change_ar": f"درجة شذوذ لا تتجاوز {t:.4f}",
                        "verdict": check, "established_by": "re-executed decision rule on the same calibration scores", "actor": "the part itself"})
    alpha = Fraction(str(a))
    need = math.ceil(1 / alpha) - 1
    if len(scores) < need:
        check = b.rank_decision([min(scores)] * need, r["score"] + 1, a)["decision"]
        out.append({"change": f"Calibrate with at least {need} independent normal parts (now {len(scores)})",
                    "change_ar": f"معايرة بـ {need} قطعة سليمة مستقلة على الأقل", "verdict": check,
                    "established_by": "decision rule executed with the minimum calibration size; outcome holds only if the part still scores above every calibration part",
                    "actor": "operator only"})
    pac = b.rank_decision(scores, r["score"], a, {"type": "pac", "delta": "0.1"})
    out.append({"change": "Require a per-bank guarantee (PAC rule, δ = 0.1)", "change_ar": "اشتراط ضمان لكل بنك (قاعدة PAC)",
                "verdict": pac["decision"], "established_by": "re-executed decision rule" + ("" if pac["k"] else
                f"; this bank is too small, PAC needs {pac['training_conditional']['minimum_m_for_pac']} parts"), "actor": "operator only"})
    return out


# ---------------------------------------------------------------- RASID ----
def rasid_sentences(r):
    c, plan, p = r["certificate"], r["plan"], r["problem"]
    charge = [h for h, v in enumerate(plan["charge"]) if v > 1e-6]
    discharge = [h for h, v in enumerate(plan["discharge"]) if v > 1e-6]
    peak = max(max(row) for row in plan["grid"])
    gap = "n/a" if c["relative_gap"] is None else f"{100 * c['relative_gap']:.3f}%"
    hours = lambda hs: ", ".join(str(h) for h in hs) or "none"
    en = (f"Plan {c['physical_status']} by the independent verifier (no solver code). Expected cost {c['expected_cost']:.2f}, "
          f"CVaR{int(100 * c['beta'])} {c['cvar']:.2f}, risk-adjusted objective {c['objective']:.2f} at risk weight {c['risk_weight']:g}; "
          f"recomputed optimality gap {gap}. The battery charges in intervals {hours(charge)} and discharges in {hours(discharge)}; "
          f"highest scenario import {peak:.1f} kW across {len(p['weights'])} weighted scenarios. Simulation only: nothing is dispatched.")
    ar = (f"الخطة {c['physical_status']} حسب المدقق المستقل (بدون كود المحلّل). التكلفة المتوقعة {c['expected_cost']:.2f}، "
          f"وCVaR{int(100 * c['beta'])} = {c['cvar']:.2f}، والهدف المعدّل بالمخاطرة {c['objective']:.2f} عند وزن مخاطرة {c['risk_weight']:g}؛ "
          f"فجوة الأمثلية المعاد حسابها {gap}. البطارية تشحن في الفترات {hours(charge)} وتفرّغ في {hours(discharge)}؛ "
          f"أعلى سحب من الشبكة {peak:.1f} كيلوواط عبر {len(p['weights'])} سيناريو. محاكاة فقط: لا يتم تشغيل أي جهاز.")
    return en, ar


def rasid_counterfactuals(r):
    from .rasid.input import checked
    from .rasid.solver import solve
    from .rasid.verify import verify
    base = r["certificate"]
    out = []
    a = r["problem"]["assets"]
    variants = [("Risk weight 0 (pure expected cost)", "وزن مخاطرة صفر", "operator policy", lambda p: p.update(risk=0.0))]
    if a["capacity"] > 0:
        variants.append((f"Battery capacity +25% ({a['capacity']:g} → {1.25 * a['capacity']:g} kWh)", "سعة بطارية أكبر بـ 25%", "investment",
                         lambda p: p["assets"].update(capacity=1.25 * p["assets"]["capacity"])))
        variants.append((f"Battery power +25% ({a['power']:g} → {1.25 * a['power']:g} kW)", "قدرة شحن وتفريغ أعلى بـ 25%", "investment",
                         lambda p: p["assets"].update(power=1.25 * p["assets"]["power"])))
    for label, label_ar, actor, change in variants:
        if label.startswith("Risk") and r["problem"]["risk"] == 0:
            continue
        p = copy.deepcopy(r["problem"])
        change(p)
        p = checked(p)
        cert = verify(p, solve(p))
        out.append({"change": label, "change_ar": label_ar, "actor": actor, "status": cert["physical_status"],
                    "expected_cost_delta": cert["expected_cost"] - base["expected_cost"],
                    "cvar_delta": cert["cvar"] - base["cvar"], "objective_delta": cert["objective"] - base["objective"],
                    "established_by": "re-solved and independently re-verified on the same scenarios"})
    return out


# ----------------------------------------------------------------- API -----
def explain(kind, result, payload=None, profile=None):
    """Bilingual explanation plus executed counterfactuals. SANAD counterfactuals need the original payload and profile."""
    kind = kind.lower()
    if kind == "sanad":
        pairs = [sanad_sentences(r) for r in result["requirements"]]
        cf = sanad_counterfactuals(payload, profile, result) if payload is not None and profile is not None else None
        note = None if cf is not None else "Counterfactuals need the original documents and profile (available in the local store)."
    elif kind == "basira":
        pairs, cf, note = [basira_sentences(result)], basira_counterfactuals(result), None
    elif kind == "rasid":
        pairs, cf, note = [rasid_sentences(result)], rasid_counterfactuals(result), None
    else:
        raise ValueError("Unknown product")
    out = {"product": kind.upper(), "engine": result.get("engine"), "en": [p[0] for p in pairs], "ar": [p[1] for p in pairs],
           "counterfactuals": cf,
           "method": "Sentences are rendered from the verified result's fields; counterfactuals are re-executed through the same engines."}
    if note:
        out["note"] = note
    return out
