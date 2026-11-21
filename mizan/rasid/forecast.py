"""A causal, frozen ridge forecaster with paired out-of-sample daily error trajectories."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import math
import numpy as np
from mizan.core.contract import digest,instant,number,require

HOUR=timedelta(hours=1)
LEVELS=np.array([.05,.1,.5,.9,.95])


def local_day(day,zone):
    start=datetime.fromisoformat(day).date();z=ZoneInfo(zone)
    a=datetime.combine(start,datetime.min.time(),tzinfo=z).astimezone(timezone.utc)
    b=datetime.combine(start+timedelta(days=1),datetime.min.time(),tzinfo=z).astimezone(timezone.utc)
    length=(b-a).total_seconds()/3600
    require(length.is_integer(),"GRID","Only integral hourly local-day grids are supported")
    return [a+i*HOUR for i in range(int(length))]


def records(rows,cutoff=None):
    require(isinstance(rows,list) and 24*14<=len(rows)<=8000,"HISTORY","Supply 14–300 days of hourly load/PV")
    out=[]
    for r in rows:
        ts,known=instant(r["time"]),instant(r["known_at"])
        if cutoff is not None and (ts>=cutoff or known>cutoff):continue
        require(ts.minute==0 and ts.second==0 and ts.microsecond==0 and known>=ts,"VINTAGE","Invalid observation vintage")
        out.append((ts,known,number(r["load"],"load",0,1e6),number(r["pv"],"PV",0,1e6)))
    return out


def vintage(rows,origin):
    v={}
    for t,known,load,pv in rows:
        if t>=origin or known>origin:continue
        if t in v and known==v[t][0]:
            require((load,pv)==v[t][1:],"DUPLICATE","Conflicting values share the same vintage")
        if t not in v or known>v[t][0]:v[t]=(known,load,pv)
    return {t:np.array(a[1:]) for t,a in v.items()}


def features(t,hist):
    day=t.hour/24*2*np.pi;week=t.weekday()/7*2*np.pi
    yesterday=hist.get(t-24*HOUR);lastweek=hist.get(t-168*HOUR)
    if yesterday is None:yesterday=lastweek
    if lastweek is None:lastweek=yesterday
    if yesterday is None:return None
    return [1,math.sin(day),math.cos(day),math.sin(2*day),math.cos(2*day),math.sin(week),math.cos(week),
            *yesterday,*lastweek]


def fit(hist):
    xs=[];ys=[]
    for t,y in sorted(hist.items()):
        x=features(t,hist)
        if x is not None:xs.append(x);ys.append(y)
    require(len(xs)>=72,"WARMUP","Not enough complete training lags")
    x=np.asarray(xs);y=np.asarray(ys);center=x.mean(0);scale=x.std(0);scale[scale<1e-7]=1
    center[0]=0;u=(x-center)/scale
    penalty=np.eye(u.shape[1])*1.5;penalty[0,0]=0
    weights=np.linalg.solve(u.T@u+penalty,u.T@y)
    return weights,center,scale


def predict(model,grid,hist):
    x=[features(t,hist) for t in grid]
    require(all(a is not None for a in x),"MISSING_LAG","No eligible lag for an interval")
    w,c,s=model
    return np.maximum(0,(np.asarray(x)-c)/s@w)


def reduce_paths(paths,cap):
    paths=np.asarray(paths);n=len(paths)
    if n<=cap:return paths,np.full(n,1/n)
    flat=paths.reshape(n,-1);scale=np.maximum(flat.std(0),1);flat=flat/scale
    distance=((flat[:,None,:]-flat[None,:,:])**2).sum(2)
    chosen=[int(np.argmin(distance.sum(0)))]
    while len(chosen)<cap:
        best=distance[:,chosen].min(1);best[chosen]=-1
        if best.max()<=1e-12:break
        chosen.append(int(np.argmax(best)))
    labels=np.argmin(distance[:,chosen],axis=1);weights=np.bincount(labels,minlength=len(chosen))/n
    return paths[np.array(chosen)[weights>0]],weights[weights>0]


def quantile_scores(levels,quantiles,y):
    levels=np.asarray(levels);q=np.asarray(quantiles);y=np.asarray(y)
    require(q.shape==y.shape+(len(levels),) and np.all(np.isfinite(q)) and np.all(np.isfinite(y)) and len(levels)>1 and
            np.all(np.diff(levels)>0) and 0<levels[0]<levels[-1]<1 and np.all(np.diff(q,axis=-1)>=0),"QUANTILES","Invalid quantile grid")
    diff=y[...,None]-q;loss=np.maximum(levels*diff,(levels-1)*diff)
    return {"mean_pinball":float(loss.mean()),"truncated_crps":float(np.mean(np.sum(np.diff(levels)*(loss[...,:-1]+loss[...,1:]),axis=-1))),
            "domain":[float(levels[0]),float(levels[-1])]}


def forecast(rows,origin,grid,max_scenarios=8):
    hist=vintage(rows,origin);available=(origin-min(hist)).days if hist else 0
    ncal=min(28,available-7);require(ncal>=7,"WARMUP","Need seven training and seven calibration days")
    cut=origin-timedelta(days=ncal);train=vintage(rows,cut);model=fit(train)
    future=predict(model,grid,hist);errors=[]
    for d in range(ncal,0,-1):
        past_origin=origin-timedelta(days=d);past_grid=[t-timedelta(days=d) for t in grid]
        if any(t not in hist for t in past_grid):continue
        past=vintage(rows,past_origin)
        if any(features(t,past) is None for t in past_grid):continue
        y=np.array([hist[t] for t in past_grid]);errors.append(y-predict(model,past_grid,past))
    require(len(errors)>=6,"CALIBRATION","Too few complete paired out-of-sample error paths")
    # The residual trajectory is the statistical unit. No independent hourly coverage claim.
    err=np.stack(errors)
    paths=np.maximum(0,future[None]+err)
    q=np.moveaxis(np.quantile(paths,LEVELS,axis=0),0,-1)
    reduced,weights=reduce_paths(paths,max_scenarios)
    naive=np.array([hist.get(t-168*HOUR,hist.get(t-24*HOUR)) for t in grid])
    return {"engine":"fixed-ridge+paired-daily-residuals/v1","origin":origin.isoformat(),"fit_cutoff":cut.isoformat(),
            "eligible_vintage_digest":digest([[t.isoformat(),v.tolist()] for t,v in sorted(hist.items())]),
            "residual_days":len(err),"levels":LEVELS.tolist(),"quantiles":q.tolist(),"point":future.tolist(),
            "seasonal_naive":naive.tolist(),"scenarios":reduced.tolist(),"weights":weights.tolist(),
            "coverage_scope":"empirical hourly marginals; dependent daily series"}
