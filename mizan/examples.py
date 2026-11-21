"""Public, deterministic fixtures for demos and regression tests."""
from datetime import datetime,timedelta,timezone
import numpy as np
from PIL import Image
from .basira.engine import encode_image,PROFILE as VISION
from .rasid.engine import PROFILE as ENERGY
from .sanad.engine import source_id,PROFILE as TEXT

def procurement():
    a={"name":"Approved technical schedule · EN","pages":["Model: Atlas-20\nRated apparent power: 20 kVA\nRuntime at 100% load: at least 12 min\nEfficiency at 100% load: 94 %\nTransfer time: 0 ms"]}
    b={"name":"Manufacturer sheet · AR","pages":["الطراز: Atlas-20\nالقدرة الظاهرية الاسمية: ١٦ kVA\nزمن التشغيل عند حمل ١٠٠٪: على الأقل ١٢ دقيقة\nالكفاءة عند حمل ١٠٠٪: ٩٤٪\nزمن التحويل: ٠ ms"]}
    return {"model":"Atlas-20","documents":[a,b],"policy":"all_sources","compare_policies":True,
            "rules":[{"attribute":"apparent_power","op":"ge","threshold":"20","unit":"kVA"},
                     {"attribute":"runtime_100","op":"ge","threshold":"10","unit":"min"},
                     {"attribute":"efficiency_100","op":"ge","threshold":"95","unit":"%"},
                     {"attribute":"transfer_time","op":"le","threshold":"0","unit":"ms"}]}


def part(seed,defect=None):
    rng=np.random.default_rng(seed);yy,xx=np.mgrid[-48:48,-48:48]
    x=xx-rng.uniform(-.8,.8);y=yy-rng.uniform(-.8,.8);r=np.hypot(x,y)
    surface=np.full(r.shape,34.)+rng.normal(0,1.8,r.shape)
    ring=(r>17)&(r<38)
    grain=4*np.sin(.5*r+rng.uniform(-.2,.2))+5*np.sin(.13*x+.08*y)
    surface[ring]=153+grain[ring]+rng.normal(0,3.1,ring.sum())
    surface += 37*np.exp(-((r-36.5)/1.1)**2)+42*np.exp(-((r-18.3)/1.3)**2)
    if defect=="crack":
        theta=rng.uniform(0,2*np.pi);along=x*np.cos(theta)+y*np.sin(theta);across=-x*np.sin(theta)+y*np.cos(theta)
        mask=(np.abs(across-1.4*np.sin(along/4))<2.0)&(along>19)&(along<36)
        surface[mask]-=97
    elif defect=="pit":
        theta=rng.uniform(0,2*np.pi);cx=28*np.cos(theta);cy=28*np.sin(theta)
        surface[np.hypot(x-cx,y-cy)<4.5]-=78
    surface=surface*rng.uniform(.96,1.04)+rng.uniform(-3,3)
    return Image.fromarray(np.uint8(np.clip(surface,0,255)))


def item(seed,defect=None):
    return {"group":f"fixture-part-{seed}","png":encode_image(part(seed,defect))}


def inspection(cal_count=24,defect="crack"):
    return {"reference":[item(i) for i in range(10)],"calibration":[item(i+100) for i in range(cal_count)],
            "query":item(450,defect),"alpha":"0.05"}


def settings(t):
    price=[.34 if 16<=h%24<22 else .07 if h%24<7 else .16 for h in range(t)]
    return {"assets":{"capacity":80.,"power":20.,"initial":40.,"terminal":40.,"eta_c":.95,"eta_d":.95,"limit":150.,"cycles":1.0},
            "tariff":{"price":price,"up":[.06]*t,"down":[.04]*t,"demand":.9,"established":85.,"wear":.012,"shed":90.,"overload":120.},
            "risk":.35,"beta":.90,"flex":[],"blocks":[]}


def electricity(days=60,seed=716):
    rng=np.random.default_rng(seed);start=datetime(2026,1,1,tzinfo=timezone.utc);data=[]
    for d in range(days):
        weather=rng.normal();cloud=np.clip(.83+rng.normal(0,.11),.5,1.1)
        for h in range(24):
            t=start+timedelta(days=d,hours=h)
            pv=max(0,np.sin((h-6)*np.pi/12))*28*cloud if 6<=h<=18 else 0
            load=51+27*np.exp(-((h-17)/4.4)**2)+10*np.exp(-((h-9)/2.8)**2)+5*(t.weekday()<5)+weather*2.1+d*.045+rng.normal(0,1.7)
            data.append(dict(time=t.isoformat(),known_at=(t+timedelta(hours=1)).isoformat(),load=round(float(load),4),pv=round(float(pv),4)))
    return data


def energy(days=60):
    rows=electricity(days);date=(datetime(2026,1,1)+timedelta(days=days)).date().isoformat()
    planning=settings(24)
    planning["flex"]=[{"start":3,"end":12,"energy":12.0,"power":4.0}]
    planning["blocks"]=[{"start":7,"end":17,"length":2,"power":3.0}]
    return {"day":date,"timezone":"UTC","history":rows,"planning":planning,"scenario_count":6}


def peak_oracle():
    p=settings(2);p.update(hours=[1,1],weights=[1],scenarios=[[[1,0],[1,0]]],risk=0,beta=.9)
    p["assets"].update(capacity=1,power=1,initial=0,terminal=0,eta_c=1,eta_d=1,limit=10)
    p["tariff"].update(price=[0,10],up=[0,0],down=[0,0],demand=1,established=0,wear=0)
    return {"problem":p}


def profile(kind):
    if kind=="sanad":
        docs=procurement()["documents"]
        return {"engine":TEXT,"approved_sources":{source_id(docs[0]):2,source_id(docs[1]):1},"mode":"CLOSED_GRAMMAR"}
    if kind=="basira":return {"engine":VISION,"mode":"ADVISORY_OR_NORMAL_ALERT"}
    if kind=="rasid":return {"engine":ENERGY,"mode":"SIMULATION"}
    raise KeyError(kind)
