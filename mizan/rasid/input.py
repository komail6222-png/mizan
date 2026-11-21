"""Physical units: kW, kWh, hours and an explicit currency unit."""
import math
import numpy as np
from mizan.core.contract import number,require

def vector(values,n,name,lo,hi):
    require(isinstance(values,list) and len(values)==n,"SHAPE",f"Wrong length for {name}")
    return [number(x,name,lo,hi) for x in values]


def checked(p):
    raw=p["scenarios"];weights=p["weights"];dt=p["hours"]
    require(isinstance(dt,list) and 1<=len(dt)<=48 and isinstance(raw,list) and 1<=len(raw)<=16,"SIZE","At most 48 steps and 16 scenarios")
    t=len(dt);s=len(raw);hours=vector(dt,t,"duration",1/60,4)
    pi=vector(weights,s,"weight",1e-10,1);require(math.isclose(math.fsum(pi),1,rel_tol=0,abs_tol=1e-10),"WEIGHTS","Scenario weights must sum to one")
    scenarios=[]
    for path in raw:
        require(isinstance(path,list) and len(path)==t,"SHAPE","Scenario time count differs")
        scenarios.append([vector(pair,2,"load/PV",0,1e6) for pair in path])
    a=p["assets"];cap=number(a["capacity"],"capacity",0,1e6);power=number(a["power"],"power",0,1e5)
    require(cap>0 or power==0,"BATTERY","Disabled battery must have zero power")
    asset=dict(capacity=cap,power=power,initial=number(a["initial"],"initial",0,cap),terminal=number(a["terminal"],"terminal",0,cap),
               eta_c=number(a["eta_c"],"eta_c",.05,1),eta_d=number(a["eta_d"],"eta_d",.05,1),
               cycles=number(a.get("cycles",1),"cycles",0,20),limit=number(a["limit"],"limit",0,1e6))
    tariff=p["tariff"]
    rates=dict(price=vector(tariff["price"],t,"price",-100,100),up=vector(tariff["up"],t,"up",0,100),
               down=vector(tariff["down"],t,"down",0,100),demand=number(tariff["demand"],"demand",0,1e5),
               established=number(tariff.get("established",0),"established",0,1e6),
               wear=number(tariff.get("wear",.01),"wear",0,100),shed=number(tariff.get("shed",100),"shed",1,1e5),
               overload=number(tariff.get("overload",120),"overload",1,1e5))
    risk=number(p["risk"],"risk",0,1);beta=number(p["beta"],"beta",0,.9999)
    flex=p.get("flex",[]);blocks=p.get("blocks",[])
    require(isinstance(flex,list) and len(flex)<=6 and isinstance(blocks,list) and len(blocks)<=4,"SIZE","Too many flexible loads")
    def window(x):
        l=x["start"];r=x["end"]
        require(type(l) is int and type(r) is int and 0<=l<r<=t,"WINDOW","Invalid load window")
        return l,r
    f=[]
    for x in flex:
        l,r=window(x);pw=number(x["power"],"flex power",0,1e4);energy=number(x["energy"],"flex energy",0,pw*sum(hours[l:r]))
        f.append(dict(start=l,end=r,power=pw,energy=energy))
    b=[]
    for x in blocks:
        l,r=window(x);k=x["length"];require(type(k) is int and 1<=k<=r-l,"BLOCK","Invalid block duration in slots")
        b.append(dict(start=l,end=r,length=k,power=number(x["power"],"block power",0,1e4)))
    return dict(scenarios=scenarios,weights=pi,hours=hours,assets=asset,tariff=rates,risk=risk,beta=beta,flex=f,blocks=b)
