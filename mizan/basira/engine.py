"""Few-shot inspection of an aligned product family using a frozen, spatial patch bank.

Release 2.3 keeps the v2 descriptor and bank identity unchanged and adds a training-conditional view of the
calibration. Split conformal guarantees P(false alert) <= alpha *on average over calibration draws*. For the one bank
that is actually deployed, the long-run false-alert rate of the order-statistic threshold s_(k) is random:
P(rate > alpha) = P(Binomial(m, 1 - alpha) >= k), computed here exactly. The optional PAC rule picks the smallest k
that bounds this probability by delta, or abstains when m is too small.
"""
import base64
import io
import math
from fractions import Fraction
import numpy as np
from PIL import Image
from mizan.core.contract import canonical,digest,number,require,Refused

DESCRIPTOR="basira/registered-patch-nn/96px/float64/v2"   # Scoring procedure. Bank and calibration identity bind to it.
PROFILE="basira/patch-nn-v2/conformal-marginal-pac/3"     # Engine output contract (adds training-conditional fields).
DEFAULT_DELTA=Fraction(1,10)
SIZE=96
GRID=12


def encode_image(image):
    buf=io.BytesIO();image.save(buf,format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def decode_image(encoded):
    require(isinstance(encoded,str) and 0<len(encoded)<=200000,"IMAGE_SIZE","Use a PNG or JPEG under 150 KB")
    try:
        data=base64.b64decode(encoded,validate=True)
        with Image.open(io.BytesIO(data)) as im:
            require(im.format in ("PNG","JPEG") and im.width*im.height<=1_000_000
                    and min(im.size)>=16 and getattr(im,"n_frames",1)==1,"IMAGE_FORMAT","Unsupported image dimensions or format")
            im.load()
            return im.convert("L").resize((SIZE,SIZE),Image.Resampling.BILINEAR)
    except (ValueError,OSError,Image.DecompressionBombError) as exc:
        if isinstance(exc,Refused):raise
        raise Refused("IMAGE_FORMAT","Could not decode input image") from exc


def features(image):
    x=np.asarray(image,dtype=np.float64)/255
    q10,q90=np.quantile(x,[.1,.9]);scale=max(q90-q10,.08)
    x=(x-np.median(x))/scale
    grad_y,grad_x=np.gradient(x)
    def blocks(v):
        return v.reshape(GRID,SIZE//GRID,GRID,SIZE//GRID).transpose(0,2,1,3).reshape(GRID,GRID,-1)
    v=blocks(x)
    from scipy.ndimage import grey_closing, grey_opening
    descriptor=[v.mean(-1),v.std(-1),v.min(-1)*.48,v.max(-1)*.48,
                blocks(np.abs(grad_x)).mean(-1)*2,blocks(np.abs(grad_y)).mean(-1)*2]
    for width in (5,9):
        for residual in (grey_closing(x,size=(width,width))-x,x-grey_opening(x,size=(width,width))):
            patches=blocks(residual)
            descriptor.extend((np.quantile(patches,.90,axis=-1)*3.0,patches.max(-1)*1.5))
    return np.stack(descriptor,axis=-1)


def fit(reference,calibration,feature_fn=features,profile=DESCRIPTOR):
    require(2<=len(reference)<=30 and 1<=len(calibration)<=100,"SPLIT_SIZE","Need 2–30 reference and 1–100 calibration items")
    groups=set();identities=set();images=[]
    for item in reference+calibration:
        group=item["group"];im=decode_image(item["png"]);key=digest(im.tobytes())
        require(isinstance(group,str) and 1<=len(group)<=100 and group not in groups and key not in identities,
                "SPLIT_LEAK","Repeated origin group or decoded image in the reference/calibration split")
        groups.add(group);identities.add(key);images.append(im)
    bank=np.stack([feature_fn(im) for im in images[:len(reference)]])
    cal=sorted(score(feature_fn(im),bank)[0] for im in images[len(reference):])
    identity=digest([profile,[(x["group"],digest(im.tobytes())) for x,im in zip(reference+calibration,images)],cal])
    return bank,cal,identity,groups,identities


def score(feat,bank,tile=8):
    require(bank.ndim==4 and feat.shape==bank.shape[1:] and np.isfinite(feat).all() and np.isfinite(bank).all(),
            "FEATURES","Incompatible or nonfinite feature arrays")
    best=np.full(feat.shape[:2],np.inf,dtype=np.float64)
    for start in range(0,len(bank),tile):
        delta=bank[start:start+tile].astype(np.float64)-feat
        dist=np.einsum("bhwd,bhwd->bhw",delta,delta,optimize=False)
        best=np.minimum(best,np.min(dist,axis=0))
    heat=np.sqrt(best)
    count=max(1,math.ceil(heat.size*.025))
    return float(np.mean(np.partition(heat.ravel(),-count)[-count:])),heat


def probability(value,label):
    try:q=Fraction(str(value))
    except (ValueError,ZeroDivisionError) as exc:raise Refused(label.upper(),f"Invalid {label}") from exc
    require(Fraction(1,1000)<=q<1 and q.denominator<=10**6,label.upper(),f"Use a {label} between 0.001 and 1")
    return q


def exceedance(m,k,alpha):
    """Exact P(long-run false-alert rate of threshold s_(k) exceeds alpha) for m exchangeable continuous scores.
    F(s_(k)) ~ Beta(k, m-k+1), so P(1-F(s_(k)) > alpha) = P(#{U_i < 1-alpha} >= k) = P(Binomial(m, 1-alpha) >= k)."""
    p=1-alpha
    return sum(math.comb(m,j)*p**j*alpha**(m-j) for j in range(k,m+1))


def pac_rank(m,alpha,delta):
    """Smallest rank whose exceedance is at most delta, or None when even the maximum is not enough."""
    p=1-alpha;tail=Fraction(0);best=None
    for k in range(m,0,-1):  # exceedance grows as k falls: accumulate from the top
        tail+=math.comb(m,k)*p**k*alpha**(m-k)
        if tail>delta:break
        best=k
    return best


def minimum_m(alpha,delta):
    """Fewest calibration parts for which the maximum score meets (alpha, delta): smallest m with (1-alpha)^m <= delta."""
    m=max(1,math.ceil(math.log(float(delta))/math.log(float(1-alpha))))
    while m>1 and (1-alpha)**(m-1)<=delta:m-=1
    while (1-alpha)**m>delta:m+=1
    return m


def guarantee_rule(spec):
    if spec in (None,"marginal"):return "marginal",None
    require(isinstance(spec,dict) and spec.get("type")=="pac" and set(spec)<={"type","delta"},"GUARANTEE",
            'Use "marginal" or {"type": "pac", "delta": "0.1"}')
    return "pac",probability(spec.get("delta",str(DEFAULT_DELTA)),"delta")


def rank_decision(scores,test,alpha,guarantee=None):
    require(len(scores)>0 and all(math.isfinite(v) for v in [*scores,test]),"NONFINITE","Invalid conformal score")
    a=Fraction(str(alpha));require(0<a<1,"ALPHA","Use an alpha strictly between zero and one")
    rule,delta=guarantee_rule(guarantee)
    m=len(scores);ordered=sorted(scores);k=math.ceil((m+1)*(1-a));hits=1+sum(v>=test for v in scores);p=Fraction(hits,m+1)
    finite=k<=m;threshold=ordered[k-1] if finite else None
    out={"p_numerator":hits,"p_denominator":m+1,"p_value":float(p),"alpha":str(a),"k":k,"threshold":threshold,
         "mode":"NORMAL_ALERTS_ONLY" if finite else "ADVISORY_INSUFFICIENT_NORMALS",
         "decision":"FLAG" if finite and p<=a else "REVIEW","automatic_pass":False,"guarantee":rule}
    target=delta or DEFAULT_DELTA;kp=pac_rank(m,a,target)
    tc={"marginal_rank":k if finite else None,"marginal_rank_exceedance":None,"pac_delta":str(target),"pac_rank":kp,
        "pac_threshold":ordered[kp-1] if kp else None,"minimum_m_for_pac":minimum_m(a,target),
        "meaning":"exceedance = probability, over calibration draws, that this threshold's long-run false-alert rate on normal parts exceeds alpha"}
    if finite:
        e=exceedance(m,k,a);tc["marginal_rank_exceedance"]=float(e);tc["marginal_rank_exceedance_exact"]=f"{e.numerator}/{e.denominator}"
    out["training_conditional"]=tc
    if rule=="pac":
        if kp is None:
            out.update(k=None,threshold=None,mode="ADVISORY_INSUFFICIENT_NORMALS_FOR_PAC",decision="REVIEW")
        else:
            # Strict comparison keeps ties conservative, exactly as the marginal rule does.
            out.update(k=kp,threshold=ordered[kp-1],mode="NORMAL_ALERTS_ONLY_PAC",decision="FLAG" if test>ordered[kp-1] else "REVIEW")
    return out


def overlay(image,heat):
    gray=np.asarray(image,dtype=np.float64)
    ref=max(float(heat.max()),1e-12)
    h=np.asarray(Image.fromarray(np.uint8(heat/ref*255)).resize(image.size,Image.Resampling.BILINEAR))/255
    blend=np.clip((h-.14)/.86,0,1)*.80
    rgb=gray[...,None]*(1-blend[...,None])+np.array([255,72,36])[None,None,:]*blend[...,None]
    return encode_image(Image.fromarray(np.uint8(np.clip(rgb,0,255))))


def check_profile(profile):
    engine=profile.get("engine")
    if engine==DESCRIPTOR:
        require(False,"PROFILE_UPGRADE_REQUIRED",f"Deployed engine {engine} predates {PROFILE}; run: python -m mizan upgrade")
    require(engine==PROFILE,"PROFILE","Unqualified inspection profile")


def evaluate(payload,profile):
    check_profile(profile)
    ref,cal=payload["reference"],payload["calibration"];query=payload["query"]
    require(isinstance(ref,list) and isinstance(cal,list),"SPLIT","Invalid split format")
    bank,scores,rev,groups,ids=fit(ref,cal)
    im=decode_image(query["png"])
    require(query["group"] not in groups and digest(im.tobytes()) not in ids,"QUERY_LEAK","Query already present in bank/calibration")
    s,heat=score(features(im),bank)
    result=rank_decision(scores,s,payload.get("alpha",".05"),payload.get("guarantee"))
    return {"product":"BASIRA","engine":PROFILE,"descriptor":DESCRIPTOR,"bank_digest":rev,"reference_count":len(ref),"calibration_count":len(cal),
            "score":s,"group":query["group"],"calibration_scores":scores,"score_map":heat.round(7).tolist(),
            "image_png":encode_image(im),"overlay_png":overlay(im,heat),
            "sensitivity_evidence":"No independent real-defect certificate for automatic PASS",**result}
