"""Evidence-first Arabic/English procurement review over a deliberately closed grammar.

Grammar 2 (release 2.2) changes, all of which create a new profile identity:
  * Arabic typography: tatweel (U+0640) is ignored; the Arabic thousands separator (U+066C) is accepted only in
    strict three-digit groups. An ASCII comma stays unsupported because 20,000 / 20,5 are locale-ambiguous.
  * Additional unit spellings (Arabic kVA/VA/kW/W/seconds/milliseconds, English sec/msec). Digits are ASCII only
    after the Arabic-Indic translation, so other scripts fall outside the grammar and abstain.
  * Operator-acknowledged context: an approval may list exact uncovered lines (page, span, SHA-256 of the quote)
    that a reviewer declared non-evidential, such as a title or a footer. Lines that name a scored attribute or
    contain a number with a scored unit can never be acknowledged; they need a corrected transcript.

Grammar 3 (release 2.3) adds delimited tables ("|" or tab). A header row names the models: its first cell (or its
last cell, for right-to-left extraction) is a model label, an optional unit column may follow it, and every other
cell must be a distinct model identifier. Each data row must have exactly the header's width. Only the offered
model's cell can become a claim, and the claim carries the row span, the exact cell span and the header span that
binds the column to the model. A shape error, an empty or dash cell, an unparseable header or a footnote marker
abstains. A table ends at the first undelimited line or page break; prose after it needs a new model heading.
"""
import math
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from mizan.core.contract import digest, require

TRANSLATE=str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٪أإآ", "01234567890123456789.%ااا")
TATWEEL="ـ"
THOUSANDS="٬"
ATTR={
    "apparent_power": ("Rated apparent power","القدرة الظاهرية الاسمية"),
    "active_power": ("Rated active power","القدرة الفعلية الاسمية"),
    "runtime_100": ("Runtime at 100% load","زمن التشغيل عند حمل 100%"),
    "efficiency_100": ("Efficiency at 100% load","الكفاءة عند حمل 100%"),
    "transfer_time": ("Transfer time","زمن التحويل"),
}
SECONDS=("s","sec","seconds","ثانية","ثوان","ثواني")
UNITS={
    "apparent_power":{"kva":"1","va":"0.001","ك ف ا":"1","كيلو فولت امبير":"1","كيلوفولت امبير":"1","فولت امبير":"0.001"},
    "active_power":{"kw":"1","w":"0.001","كيلوواط":"1","كيلو واط":"1","واط":"0.001"},
    "runtime_100":{"min":"1","minute":"1","minutes":"1","دقيقة":"1","دقائق":"1",**{s:"1/60" for s in SECONDS}},
    "efficiency_100":{"%":"1"},
    "transfer_time":{"ms":"1","msec":"1","milliseconds":"1","مللي ثانية":"1","ملي ثانية":"1","ميلي ثانية":"1"},
}
BASE={"apparent_power":"kVA","active_power":"kW","runtime_100":"min","efficiency_100":"%","transfer_time":"ms"}
QUAL={"up to":"upper","at most":"upper","بحد اقصى":"upper","حتى":"upper","at least":"lower","على الاقل":"lower",
      "optional":"unknown","اختياري":"unknown","typical":"unknown","نموذجي":"unknown","not included":"unknown","غير مشمول":"unknown"}
PROFILE="sanad/closed-ups-grammar/3"
PROFILE_FAMILY="sanad/closed-ups-grammar/"
MODEL_LABELS=("model","الطراز","الموديل")
TABLE_LABELS=MODEL_LABELS+("models","الطرازات","الموديلات")
UNIT_LABELS=("unit","units","الوحدة","الوحدات")
EMPTY_CELLS=("","-","--","\u2013","\u2014","n/a","na","غير متوفر")
SEPARATOR=re.compile(r":?-{2,}:?")
MODEL_ID=re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
NUMBER=r"[0-9]{1,3}(?:"+THOUSANDS+r"[0-9]{3}){1,2}(?:\.[0-9]{1,5})?|[0-9]{1,7}(?:\.[0-9]{1,5})?"
VALUE=re.compile(r"(?P<value>"+NUMBER+r")\s*(?P<unit>[%a-z؀-ۿ][%a-z؀-ۿ ]*)")
THRESHOLD=re.compile(r"[0-9]{1,7}(?:\.[0-9]{1,5})?")
MAX_ACKNOWLEDGED=200


def normal(text):
    s=unicodedata.normalize("NFKC",text).translate(TRANSLATE).lower()
    s="".join(c for c in s if not 0x64b<=ord(c)<=0x65f and c!=TATWEEL)
    return " ".join(s.split())


ALIASES={normal(label):attr for attr,labels in ATTR.items() for label in labels}
ALL_UNITS=sorted({u for table in UNITS.values() for u in table},key=len,reverse=True)
MEASURE=re.compile(r"[0-9][0-9"+THOUSANDS+r".]*\s*(?:"+"|".join(map(re.escape,ALL_UNITS))+r")(?![a-z؀-ۿ])")


def source_id(doc):
    return digest({"pages":doc["pages"],"original_file":doc["original_file_sha256"]}) if "original_file_sha256" in doc else digest({"pages":doc["pages"]})


def quote_digest(quote):
    return digest(quote.encode("utf-8"))


def parse_value(text, attr):
    q="exact"
    for tag,kind in sorted(QUAL.items(),key=lambda x:-len(x[0])):
        if text.startswith(tag+" "):
            text=text[len(tag):].strip();q=kind;break
        if text.endswith(" "+tag):
            text=text[:-len(tag)].strip();q=kind;break
    match=VALUE.fullmatch(text)
    if not match:return None
    unit=match["unit"].strip()
    if unit not in UNITS[attr]:return None
    v=Decimal(match["value"].replace(THOUSANDS,""))
    # Seconds convert exactly by division, avoiding rounded conversion constants at boundaries.
    if UNITS[attr][unit]=="1/60":
        return str(Fraction(v)/60),q
    v*=Decimal(UNITS[attr][unit])
    if attr=="efficiency_100" and v>100:return None
    return str(v),q


def acknowledgeable(quote):
    """Whether an operator may declare this uncovered line non-evidential. Conservative by construction."""
    if any(unicodedata.category(c) in ("Cf","Cc") for c in quote if c!="\t"):return False
    text=normal(quote)
    return not any(alias in text for alias in ALIASES) and not MEASURE.search(text)


def classify(line):
    """One closed-grammar decision per line, shared by the parser and the operator review view."""
    if any(unicodedata.category(c) in ("Cf","Cc") for c in line if c!="\t"):return "UNSAFE_TEXT_BOUNDARY",None
    left,sep,right=normal(line).partition(":");left=left.strip()
    if sep and left in MODEL_LABELS:
        model=right.strip()
        return ("MODEL",model) if MODEL_ID.fullmatch(model) else ("AMBIGUOUS_MODEL",None)
    if sep and left in ALIASES:
        attr=ALIASES[left];value=parse_value(right.strip(),attr)
        return ("CLAIM",(attr,value)) if value else ("UNSUPPORTED_VALUE_OR_CONDITION",attr)
    return "UNCOVERED_CONTEXT",None


def check_pages(doc):
    pages=doc["pages"]
    require(isinstance(pages,list) and 1<=len(pages)<=40 and all(isinstance(p,str) and len(p)<=40000 for p in pages),"PAGE_LIMIT","Expected a bounded list of text pages")
    return pages


def page_lines(page):
    offset=0
    for raw in page.splitlines(keepends=True):
        line=raw.rstrip("\r\n");start=offset;offset+=len(raw)
        if line.strip():yield start,line


def split_cells(line):
    """Cells of a delimited row with their offsets in the line. One empty edge cell on each side is a Markdown pipe."""
    delimiter="|" if "|" in line else "\t"
    pieces=[];pos=0
    for piece in line.split(delimiter):
        pieces.append((piece,pos));pos+=len(piece)+1
    if len(pieces)>1 and not pieces[0][0].strip():pieces=pieces[1:]
    if len(pieces)>1 and not pieces[-1][0].strip():pieces=pieces[:-1]
    cells=[]
    for text,offset in pieces:
        stripped=text.strip();lead=len(text)-len(text.lstrip())
        cells.append((stripped,offset+lead,offset+lead+len(stripped)))
    return cells


def parse_header(cells):
    """None (not a header), "INVALID" (a model label with unusable model identifiers), or the column layout."""
    norm=[normal(c[0]) for c in cells]
    if len(cells)<2:return None
    if norm[0] in TABLE_LABELS:order=list(range(len(cells)))
    elif norm[-1] in TABLE_LABELS:order=list(range(len(cells)))[::-1]
    else:return None
    unit=len(order)>2 and norm[order[1]] in UNIT_LABELS
    positions=order[2:] if unit else order[1:]
    models=[norm[i] for i in positions]
    if not models or len(set(models))!=len(models) or not all(MODEL_ID.fullmatch(m) for m in models):return "INVALID"
    return {"width":len(cells),"label":order[0],"unit":order[1] if unit else None,"models":dict(zip(models,positions)),
            "right_to_left":order[0]!=0}


def scan(page):
    """Resolve table context for each non-empty line. Yields (start, line, kind, data)."""
    table=None
    for start,line in page_lines(page):
        if any(unicodedata.category(c) in ("Cf","Cc") for c in line if c!="\t"):
            table=None;yield start,line,"UNSAFE_TEXT_BOUNDARY",None;continue
        if "|" in line or "\t" in line:
            cells=split_cells(line);header=parse_header(cells)
            if header=="INVALID":
                table="BROKEN";yield start,line,"AMBIGUOUS_MODEL",None;continue
            if header:
                table=dict(header,start=start,end=start+len(line),quote=line);yield start,line,"TABLE_HEADER",table;continue
            if table is not None:
                if all(SEPARATOR.fullmatch(c[0]) or not c[0] for c in cells):
                    yield start,line,"TABLE_SEPARATOR",None;continue
                if table=="BROKEN" or len(cells)!=table["width"]:
                    yield start,line,"TABLE_SHAPE",None if table=="BROKEN" else table;continue
                attr=ALIASES.get(normal(cells[table["label"]][0]))
                row={"attribute":attr,"table":table,"cells":{m:cells[i] for m,i in table["models"].items()},
                     "unit":cells[table["unit"]] if table["unit"] is not None else None}
                yield start,line,"TABLE_ROW",row;continue
        else:
            table=None
        kind,data=classify(line)
        yield start,line,kind,data


def table_value(row,model):
    text,_,_=row["cells"][model]
    if normal(text) in EMPTY_CELLS:return None
    if row["unit"] is not None:text=text+" "+row["unit"][0]
    return parse_value(normal(text),row["attribute"])


def parse_document(doc, model):
    pages=check_pages(doc)
    sid=source_id(doc);found=[];gaps=[];wanted=normal(model)
    for pageno,page in enumerate(pages,1):
        active=None;in_table=False
        for start,line,kind,data in scan(page):
            place=dict(document=doc["name"],source=sid,page=pageno,start=start,end=start+len(line),quote=line)
            if kind.startswith("TABLE_") or kind=="AMBIGUOUS_MODEL":
                in_table=True;active=None  # Prose after a table needs a new model heading.
            elif in_table:
                in_table=False
            if kind=="UNSAFE_TEXT_BOUNDARY":
                gaps.append({**place,"reason":kind});continue
            if kind in ("TABLE_HEADER","TABLE_SEPARATOR"):continue
            if kind=="TABLE_SHAPE":
                if data is None or wanted in data["models"]:gaps.append({**place,"reason":kind})
                continue
            if kind=="TABLE_ROW":
                table=data["table"]
                if wanted not in table["models"]:continue
                if data["attribute"] is None:
                    gaps.append({**place,"reason":"UNCOVERED_CONTEXT"});continue
                attr=data["attribute"];value=table_value(data,wanted)
                if value is None:
                    gaps.append({**place,"attribute":attr,"reason":"UNSUPPORTED_VALUE_OR_CONDITION"});continue
                text,a,b=data["cells"][wanted];v,q=value
                claim={**place,"attribute":attr,"value":v,"base_unit":BASE[attr],"qualifier":q,
                       "cell":{"column":wanted,"start":start+a,"end":start+b,"text":text},
                       "table_header":{"page":pageno,"start":table["start"],"end":table["end"],"quote":table["quote"]}}
                if data["unit"] is not None:
                    claim["unit_cell"]={"start":start+data["unit"][1],"end":start+data["unit"][2],"text":data["unit"][0]}
                found.append(claim);continue
            if kind=="MODEL":
                active=data;continue
            if kind=="AMBIGUOUS_MODEL":
                gaps.append({**place,"reason":kind});active=None;continue
            if active is not None and active!=wanted:continue
            if not active or kind=="UNCOVERED_CONTEXT":
                gaps.append({**place,"reason":"UNCOVERED_CONTEXT"});continue
            if kind=="UNSUPPORTED_VALUE_OR_CONDITION":
                gaps.append({**place,"attribute":data,"reason":kind});continue
            attr,(v,q)=data
            found.append({**place,"attribute":attr,"value":v,"base_unit":BASE[attr],"qualifier":q})
    return {"source":sid,"claims":found,"gaps":gaps}


def review_document(doc):
    """Operator approval view: every line the grammar will not turn into a claim, with acknowledgement eligibility.
    Table rows of scored attributes are listed when any model's cell is unusable; they can never be acknowledged."""
    pages=check_pages(doc);rows=[]
    for pageno,page in enumerate(pages,1):
        for start,line,kind,data in scan(page):
            if kind in ("MODEL","CLAIM","TABLE_HEADER","TABLE_SEPARATOR"):continue
            if kind=="TABLE_ROW":
                if data["attribute"] is not None:
                    if all(table_value(data,m) is not None for m in data["cells"]):continue
                    kind="UNSUPPORTED_VALUE_OR_CONDITION"
                else:kind="UNCOVERED_CONTEXT"
            rows.append(dict(page=pageno,start=start,end=start+len(line),quote=line,quote_sha256=quote_digest(line),reason=kind,
                             acknowledgeable=kind=="UNCOVERED_CONTEXT" and acknowledgeable(line)))
    return {"source":source_id(doc),"lines":rows}


def approval(entry):
    """Authority level and acknowledged spans. A bare integer is the release 2.1 form."""
    if type(entry) is int:
        level,acks=entry,[]
    else:
        require(isinstance(entry,dict) and set(entry)<={"level","acknowledged"},"APPROVAL","Malformed source approval")
        level,acks=entry.get("level"),entry.get("acknowledged",[])
    require(type(level) is int and 1<=level<=3,"APPROVAL","Authority level must be 1, 2 or 3")
    require(isinstance(acks,list) and len(acks)<=MAX_ACKNOWLEDGED,"APPROVAL","Too many acknowledged lines")
    keys=set()
    for a in acks:
        require(isinstance(a,dict) and set(a)=={"page","start","end","quote_sha256"}
                and all(type(a[k]) is int and a[k]>=0 for k in ("page","start","end"))
                and isinstance(a["quote_sha256"],str) and re.fullmatch(r"[0-9a-f]{64}",a["quote_sha256"]),
                "APPROVAL","Malformed acknowledged line")
        keys.add((a["page"],a["start"],a["end"],a["quote_sha256"]))
    return level,frozenset(keys)


def split_gaps(gaps,acknowledged):
    blocking,accepted=[],[]
    for g in gaps:
        key=(g["page"],g["start"],g["end"],quote_digest(g["quote"]))
        # Eligibility is re-checked here: a signed profile cannot acknowledge a measurable statement.
        if g["reason"]=="UNCOVERED_CONTEXT" and key in acknowledged and acknowledgeable(g["quote"]):accepted.append(g)
        else:blocking.append(g)
    return blocking,accepted


def retrieve(claims,query):
    # Character trigrams rank excerpts for display. Eligibility is decided over the full covered corpus.
    def terms(s):
        s=" "+normal(s)+" ";return Counter(s[i:i+3] for i in range(max(0,len(s)-2)))
    q=terms(query)
    features=[terms(c["quote"]) for c in claims]
    df=Counter(term for row in features for term in row)
    def sim(row):
        idf=lambda word:1+math.log((len(features)+1)/(df[word]+1))
        scale=lambda a:sum((v*idf(k))**2 for k,v in a.items())**.5
        return sum(q[k]*v*idf(k)**2 for k,v in row.items())/(scale(q)*scale(row) or 1)
    return [dict(quote=c["quote"],page=c["page"],source=c["source"],similarity=round(sim(row),6))
            for c,row in sorted(zip(claims,features),key=lambda pair:-sim(pair[1]))[:4]]


def prepare(payload):
    docs=payload.get("documents",[])
    require(0<len(docs)<=12 and isinstance(payload.get("model"),str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",payload["model"]),"BUNDLE","Invalid model or document count")
    model=payload["model"]
    for d in docs:
        require(isinstance(d,dict) and isinstance(d.get("name"),str) and 1<=len(d["name"])<=120,"DOCUMENT","Missing document name")
    pieces=[parse_document(d,model) for d in docs]
    require(len({x["source"] for x in pieces})==len(pieces),"DUPLICATE_DOCUMENT","Duplicate document pages")
    return {"fingerprint":digest([PROFILE,model,[(d["name"],x["source"]) for d,x in zip(docs,pieces)]]),"documents":pieces}


def assess(prepared,rules,policy,mode):
    require(mode in ("all_sources","highest_authority"),"POLICY","Unknown authority policy")
    require(isinstance(rules,list) and 0<len(rules)<=20,"RULES","Expected 1–20 rules")
    levels=policy.get("approved_sources",{})
    require(isinstance(levels,dict),"APPROVAL","Approved sources must be a mapping")
    trusted=[]
    for d in prepared["documents"]:
        if d["source"] in levels:
            rank,acks=approval(levels[d["source"]])
            blocking,accepted=split_gaps(d["gaps"],acks)
            trusted.append((rank,d,blocking,accepted))
    acknowledged=[g for _,_,_,accepted in trusted for g in accepted]
    matrix=[]
    for rule in rules:
        attr=rule["attribute"];op=rule["op"]
        require(attr in ATTR and op in ("ge","le") and rule.get("unit")==BASE[attr],"RULE","Unsupported attribute, comparison or unit")
        raw=str(rule["threshold"]);require(THRESHOLD.fullmatch(raw),"RULE","Invalid nonnegative threshold")
        limit=Fraction(Decimal(raw))
        claims=[(rank,c) for rank,d,_,_ in trusted for c in d["claims"] if c["attribute"]==attr]
        relevant=claims
        if mode=="highest_authority" and claims:
            top=max(rank for rank,c in claims);relevant=[(r,c) for r,c in claims if r==top]
        gaps=[g for _,_,blocking,_ in trusted for g in blocking if g.get("attribute",attr)==attr]
        evidence=[c for _,c in relevant];low=Fraction(0);high=None;reason="SUPPORTED_INTERVAL"
        if not evidence or gaps or any(c["qualifier"]=="unknown" for c in evidence):
            verdict="REVIEW";reason="INCOMPLETE_CONTEXT" if gaps else "UNSUPPORTED_OR_MISSING_EVIDENCE"
        else:
            for c in evidence:
                v=Fraction(c["value"]);q=c["qualifier"]
                if q in ("exact","lower"):low=max(low,v)
                if q in ("exact","upper"):high=v if high is None else min(high,v)
            if high is not None and high<low:verdict="REVIEW";reason="SOURCE_CONFLICT"
            elif op=="ge" and low>=limit or op=="le" and high is not None and high<=limit:verdict="MEETS"
            elif op=="ge" and high is not None and high<limit or op=="le" and low>limit:verdict="DOES_NOT_MEET"
            else:verdict="REVIEW";reason="BOUND_IS_INCONCLUSIVE"
        matrix.append({"rule":rule,"verdict":verdict,"reason":reason,"evidence":evidence,"coverage_gaps":gaps,
                       "acknowledged_context":acknowledged,
                       "retrieved":retrieve([c for _,c in claims], " ".join(ATTR[attr]))})
    dep=digest([prepared["fingerprint"],rules,levels,mode])
    return dict(mode=mode,evaluation_digest=dep,extraction_digest=prepared["fingerprint"],requirements=matrix,
                covered_sources=len(trusted),unapproved_sources=len(prepared["documents"])-len(trusted),
                acknowledged_lines=len(acknowledged))


def check_profile(profile):
    engine=profile.get("engine")
    if isinstance(engine,str) and engine.startswith(PROFILE_FAMILY) and engine!=PROFILE:
        require(False,"PROFILE_UPGRADE_REQUIRED",f"Deployed grammar {engine} predates {PROFILE}; run: python -m mizan upgrade")
    require(engine==PROFILE,"PROFILE","Unqualified procurement profile")


def evaluate(payload,profile):
    check_profile(profile)
    prepared=prepare(payload);rules=payload["rules"]
    mode=payload.get("policy","all_sources")
    current=assess(prepared,rules,profile,mode)
    answer=dict(product="SANAD",model=payload["model"],engine=PROFILE,**current)
    if payload.get("compare_policies",False):
        other="highest_authority" if mode=="all_sources" else "all_sources"
        answer["policy_comparison"]=assess(prepared,rules,profile,other)
        answer["extraction_passes"]=1
    return answer
