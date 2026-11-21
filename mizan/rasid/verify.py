"""Independent physical-unit verifier. This module has no solver or builder imports."""
import math
import numpy as np
from mizan.core.contract import number,require,Refused,digest


def tail_cost(cost,pi,beta):
    require(0<=beta<1 and len(cost)==len(pi)>0 and all(math.isfinite(x) for x in [*cost,*pi]) and
            all(w>0 for w in pi) and abs(math.fsum(pi)-1)<1e-10,"RISK","Invalid risk distribution")
    left=1-beta;terms=[]
    for c,w in sorted(zip(cost,pi),key=lambda x:-x[0]):
        prob=min(left,w);terms.append(c*prob);left-=prob
        if left<=0:break
    return math.fsum(terms)/(1-beta)


def verify(p,raw):
    a=p["assets"];rt=p["tariff"];sc=np.array(p["scenarios"]);pi=p["weights"];S,T,_=sc.shape
    dt=np.asarray(p["hours"]);L,V=sc[:,:,0],sc[:,:,1]
    def arr(key,shape):
        require(key in raw,"PLAN_SHAPE","Missing "+key)
        v=np.asarray(raw[key],dtype=np.float64)
        if len(shape)==2 and shape[0]==0 and v.size==0:v=v.reshape(shape)
        require(v.shape==shape and np.all(np.isfinite(v)),"PLAN_SHAPE","Nonfinite or malformed "+key)
        return v
    residuals={}
    def check(name,values,tol=2e-5):
        values=np.asarray(values);require(np.all(np.isfinite(values)),"NONFINITE",name)
        worst=max(0.,float(np.max(values))) if values.size else 0.
        residuals[name]=max(residuals.get(name,0.),worst)
        require(worst<=tol,"PLAN_REJECTED",f"{name}: {worst:.8g} exceeds {tol:g}")
    pc,pd,n,z=[arr(key,(T,)) for key in ("charge","discharge","nomination","mode")]
    e=arr("soc",(T+1,));g,u,o,curt,shed,w=[arr(k,(S,T)) for k in ("grid","up","down","curtail","shed","overload")]
    peak=arr("peak",(S,));cost=arr("scenario_cost",(S,))
    for label,v in [("flows",np.stack([g,u,o,curt,shed,w])),("battery",np.stack([pc,pd,n])),("peak",peak)]:
        check("nonnegative_"+label,-v)
    cap_nom=float(np.max(L)+sum(j["power"] for j in p["flex"]+p["blocks"])+a["power"]+1)
    check("nomination_cap",n-cap_nom)
    check("C4_initial",abs(e[0]-a["initial"]))
    recomputed=a["initial"]+np.r_[0,np.cumsum(dt*(a["eta_c"]*pc-pd/a["eta_d"]))]
    check("C4_dynamics",abs(e-recomputed));check("C4_lower",-e);check("C4_upper",e-a["capacity"]);check("C4_terminal",a["terminal"]-e[-1])
    check("C5_binary",np.abs(z-np.rint(z)),1e-7);check("C5_domain",np.maximum(-z,z-1))
    check("C5_charge",pc-a["power"]*z);check("C5_discharge",pd-a["power"]*(1-z));check("C5_exclusive",np.minimum(pc,pd))
    check("C6_cycles",math.fsum(pd*dt)-a["cycles"]*a["capacity"])
    f=np.zeros(T);q=arr("interruptible",(len(p["flex"]),T));starts=arr("block_start",(len(p["blocks"]),T))
    for j,job in enumerate(p["flex"]):
        allowed=np.zeros(T);allowed[job["start"]:job["end"]]=job["power"]
        check("C7_window_bounds",np.maximum(-q[j],q[j]-allowed))
        check("C7_energy",abs(math.fsum(q[j]*dt)-job["energy"]));f+=q[j]
    for j,jb in enumerate(p["blocks"]):
        v=starts[j];check("C8_integrality",abs(v-np.rint(v)),1e-7);check("C8_domain",np.maximum(-v,v-1))
        check("C8_start_count",abs(math.fsum(v)-1))
        h=int(np.argmax(v));require(jb["start"]<=h and h+jb["length"]<=jb["end"],"PLAN_REJECTED","C8 start outside window")
        f[h:h+jb["length"]]+=jb["power"]
    check("C1_balance",np.abs(g+V-curt+pd-pc+shed-L-f))
    check("C2_nomination",np.abs(g-n-u+o));check("C3_import",g-a["limit"]-w)
    check("C9_peak",g-rt["established"]-peak[:,None]);check("C10_curtail",curt-V);check("C10_shed",shed-L)
    # Reconstruct from day-ahead charges. Builder expresses this using spot import and imbalance premiums.
    price,up,dn=map(np.asarray,[rt["price"],rt["up"],rt["down"]])
    positive=np.maximum(g-n,0);negative=np.maximum(n-g,0)
    excess=np.maximum(g-a["limit"],0);increment=np.maximum(g.max(axis=1)-rt["established"],0)
    check("C3_actual_overload",np.abs(w-excess))
    check("C9_actual_peak",np.abs(peak-increment))
    check("C2_actual_imbalance",np.maximum(np.abs(u-positive),np.abs(o-negative)))
    rates=price*n+(price+up)*positive-(price-dn)*negative+rt["wear"]*pd+rt["shed"]*shed+rt["overload"]*excess
    derived=[math.fsum(dt*row)+rt["demand"]*pk for row,pk in zip(rates,increment)]
    check("scenario_cost",np.abs(cost-derived),1e-4)
    ev=math.fsum(v*p0 for v,p0 in zip(derived,pi));cv=tail_cost(derived,pi,p["beta"])
    k=p["risk"];obj=(1-k)*ev+k*cv
    reported=number(raw["solver"]["objective"],"objective",-1e12,1e12)
    if k:
        pivot=arr("var_pivot",(1,))[0];ex=arr("tail_excess",(S,))
        check("C11_nonnegative",-ex);check("C11_epigraph",cost-pivot-ex)
        check("C11_raw_objective",abs((1-k)*ev+k*(pivot+math.fsum(ex*np.asarray(pi))/(1-p["beta"]))-reported),1e-4)
    check("risk_objective",abs(obj-reported),1e-4)
    lb=raw["solver"].get("lower_bound");gap=None
    if lb is not None:
        number(lb,"lower bound",-1e12,1e12);check("bound_order",lb-obj,1e-4)
        gap=max(0,obj-lb)/max(1,abs(obj),abs(lb))
    slack=float(np.max(w));lost=float(np.max(shed))
    status="VERIFIED_RELAXED_DIAGNOSTIC" if max(slack,lost)>2e-5 else "VERIFIED_FEASIBLE"
    return {"physical_status":status,"expected_cost":ev,"cvar":cv,"objective":obj,"beta":p["beta"],"risk_weight":k,
            "case_costs":derived,"relative_gap":gap,"max_shed_kw":lost,"max_overload_kw":slack,"residuals":residuals,
            "input_digest":digest(p),"plan_digest":digest(raw),"verifier":"independent-physical-v2"}
