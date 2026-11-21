"""Sparse two-stage MILP. Only this module constructs the optimization matrix."""
import math
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from scipy.sparse import coo_matrix
from mizan.core.contract import require,Refused


class Matrix:
    def __init__(self):
        self.low=[];self.high=[];self.integer=[];self.cost=[];self.names={}
        self.rows=[];self.cols=[];self.val=[];self.lhs=[];self.rhs=[]

    def variable(self,name,shape,lo=0,hi=np.inf,binary=False):
        size=int(np.prod(shape));offset=len(self.low)
        out=np.arange(offset,offset+size,dtype=int).reshape(shape)
        self.low.extend(np.broadcast_to(lo,shape).ravel());self.high.extend(np.broadcast_to(hi,shape).ravel())
        self.integer.extend([int(binary)]*size);self.cost.extend([0.]*size);self.names[name]=out
        return out

    def row(self,pairs,lo=-np.inf,hi=np.inf):
        row=len(self.lhs);self.lhs.append(lo);self.rhs.append(hi)
        for i,a in pairs:
            if a:self.rows.append(row);self.cols.append(int(i));self.val.append(a)


def solve(p,time_limit=12):
    sc=np.asarray(p["scenarios"]);s,t,_=sc.shape;L,V=sc[:,:,0],sc[:,:,1]
    dt=np.array(p["hours"]);pi=p["weights"];a=p["assets"];rate=p["tariff"];k=p["risk"];beta=p["beta"]
    cap,power=a["capacity"],a["power"];m=Matrix();var=m.variable
    extra=sum(j["power"] for j in p["flex"]+p["blocks"])
    bound=float(np.max(L)+extra+power+1)
    n=var("nomination",(t,),hi=bound);pc=var("charge",(t,),hi=power);pd=var("discharge",(t,),hi=power)
    e=var("soc",(t+1,),hi=cap);mode=var("mode",(t,),hi=1,binary=True)
    g=var("grid",(s,t),hi=bound);u=var("up",(s,t),hi=bound);o=var("down",(s,t),hi=bound)
    curt=var("curtail",(s,t),hi=V);shed=var("shed",(s,t),hi=L);w=var("overload",(s,t),hi=bound)
    peak=var("peak",(s,),hi=bound);cost=var("scenario_cost",(s,),lo=-np.inf)
    q=var("interruptible",(len(p["flex"]),t))
    starts=var("block_start",(len(p["blocks"]),t),hi=1,binary=True)
    m.row([(e[0],1)],a["initial"],a["initial"]);m.row([(e[-1],1)],lo=a["terminal"])
    for j,jb in enumerate(p["flex"]):
        for h in range(t):m.high[q[j,h]]=jb["power"] if jb["start"]<=h<jb["end"] else 0
        m.row([(q[j,h],dt[h]) for h in range(t)],jb["energy"],jb["energy"])
    for j,jb in enumerate(p["blocks"]):
        for h in range(t):
            if not jb["start"]<=h<=jb["end"]-jb["length"]:m.high[starts[j,h]]=0
        m.row([(v,1) for v in starts[j]],1,1)
    for h in range(t):
        m.row([(e[h+1],1),(e[h],-1),(pc[h],-a["eta_c"]*dt[h]),(pd[h],dt[h]/a["eta_d"])],0,0)
        m.row([(pc[h],1),(mode[h],-power)],hi=0);m.row([(pd[h],1),(mode[h],power)],hi=power)
    m.row([(pd[h],dt[h]) for h in range(t)],hi=a["cycles"]*cap)
    for j in range(s):
        charge=[(cost[j],1),(peak[j],-rate["demand"])]
        for h in range(t):
            flex=[(q[b,h],-1) for b in range(len(p["flex"]))]
            for b,jb in enumerate(p["blocks"]):
                flex += [(starts[b,i],-jb["power"]) for i in range(max(0,h-jb["length"]+1),h+1)]
            m.row([(g[j,h],1),(curt[j,h],-1),(pd[h],1),(pc[h],-1),(shed[j,h],1)]+flex,L[j,h]-V[j,h],L[j,h]-V[j,h])
            m.row([(g[j,h],1),(n[h],-1),(u[j,h],-1),(o[j,h],1)],0,0)
            m.row([(g[j,h],1),(w[j,h],-1)],hi=a["limit"])
            m.row([(g[j,h],1),(peak[j],-1)],hi=rate["established"])
            # Equivalent day-ahead settlement after imposing nomination balance.
            for v,r in [(g[j,h],rate["price"][h]),(u[j,h],rate["up"][h]),(o[j,h],rate["down"][h]),
                        (pd[h],rate["wear"]),(shed[j,h],rate["shed"]),(w[j,h],rate["overload"])]:
                charge.append((v,-dt[h]*r))
        m.row(charge,0,0);m.cost[cost[j]]=(1-k)*pi[j]
    if k>0:
        z=var("var_pivot",(1,),lo=-np.inf);tail=var("tail_excess",(s,));m.cost[z[0]]=k
        for j in range(s):
            m.row([(cost[j],1),(z[0],-1),(tail[j],-1)],hi=0)
            m.cost[tail[j]]=k*pi[j]/(1-beta)
    require(len(m.val)<=300000 and len(m.cost)<=16000,"MODEL_SIZE","Sparse model exceeds reference profile")
    A=coo_matrix((m.val,(m.rows,m.cols)),shape=(len(m.lhs),len(m.cost))).tocsc()
    res=milp(np.array(m.cost),integrality=np.array(m.integer),bounds=Bounds(m.low,m.high),
             constraints=LinearConstraint(A,m.lhs,m.rhs),options={"time_limit":float(time_limit),"mip_rel_gap":1e-5})
    require(res.x is not None and res.status in (0,1),"NO_INCUMBENT","Solver returned no acceptable incumbent")
    raw={name:res.x[idx].tolist() for name,idx in m.names.items()}
    # Settle the physical dispatch, including scenarios with zero marginal weight in a pure tail objective.
    grid=np.asarray(raw["grid"]);delta=grid-np.asarray(raw["nomination"])
    upward,downward=np.maximum(delta,0),np.maximum(-delta,0)
    excess=np.maximum(grid-a["limit"],0);peak_charge=np.maximum(grid.max(axis=1)-rate["established"],0)
    for name,value in (("up",upward),("down",downward),("overload",excess),("peak",peak_charge)):
        raw[name]=value.tolist()
    settlement=(grid*np.asarray(rate["price"])+upward*np.asarray(rate["up"])+downward*np.asarray(rate["down"])
                +np.asarray(raw["discharge"])*rate["wear"]+np.asarray(raw["shed"])*rate["shed"]+excess*rate["overload"])
    costs=np.sum(settlement*dt,axis=1)+peak_charge*rate["demand"]
    raw["scenario_cost"]=costs.tolist()
    average=float(np.dot(pi,costs));objective=average
    if k>0:
        order=np.argsort(costs,kind="stable")
        idx=min(s-1,int(np.searchsorted(np.cumsum(np.asarray(pi)[order]),beta,side="left")))
        pivot=float(costs[order[idx]]);over=np.maximum(costs-pivot,0)
        raw["var_pivot"]=[pivot];raw["tail_excess"]=over.tolist()
        objective=(1-k)*average+k*(pivot+float(np.dot(pi,over))/(1-beta))
    raw["solver"]={"status":int(res.status),"objective":objective,"raw_incumbent_objective":float(res.fun),"lower_bound":float(res.mip_dual_bound)
                   if getattr(res,"mip_dual_bound",None) is not None and np.isfinite(res.mip_dual_bound) else None,
                   "rows":len(m.lhs),"columns":len(m.cost),"nnz":int(A.nnz)}
    return raw
