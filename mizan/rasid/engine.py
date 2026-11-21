from mizan.core.contract import require
from .forecast import records,forecast,local_day
from .input import checked
from .solver import solve
from .verify import verify

PROFILE="rasid/ridge-paired-residuals-scipy-milp/v2"

def materialize(payload):
    if "history" not in payload:return checked(payload["problem"]),None
    grid=local_day(payload["day"],payload.get("timezone","UTC"))
    f=forecast(records(payload["history"],grid[0]),grid[0],grid,max_scenarios=payload.get("scenario_count",6))
    p={**payload["planning"],"hours":[1.0]*len(grid),"weights":f["weights"],"scenarios":f["scenarios"]}
    f["timestamps"]=[x.isoformat() for x in grid]
    return checked(p),f


def evaluate(payload,profile):
    require(profile["engine"]==PROFILE,"PROFILE","Unqualified planning profile")
    if "history" in payload:
        count=payload.get("scenario_count",6);require(type(count) is int and 1<=count<=16,"SCENARIOS","Choose 1–16 scenarios")
    p,f=materialize(payload);plan=solve(p)
    return dict(product="RASID",engine=PROFILE,forecast=f,plan=plan,certificate=verify(p,plan),problem=p)
