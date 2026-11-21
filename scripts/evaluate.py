import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mizan.evaluation import evaluate
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--days",type=int,default=60);p.add_argument("--out",default="reports/evaluation")
    args=p.parse_args()
    if not 1<=args.days<=120:p.error("--days must be 1–120")
    result=evaluate(args.out,args.days)
    hidden=("rows","origins","scope","note","bank_digest","grammar_2_extension","grammar_3_tables","environment",
            "calibration_study","decision_quality","decision_quality_note","paired_comparisons")
    for k,r in result.items():
        print(k,{i:v for i,v in r.items() if i not in hidden})
    for name in ("grammar_2_extension","grammar_3_tables"):
        ext=result["sanad"][name]
        print("sanad",name,{k:v for k,v in ext.items() if k not in ("rows","note")})
    study=result["basira"]["calibration_study"]
    print("basira shipped calibration",{k:round(v,4) if isinstance(v,float) else v for k,v in study["shipped_calibration"].items()})
    for row in study["rows"]:
        print("basira study m=%d"%row["m"],{k:round(v,4) if isinstance(v,float) else v for k,v in row.items() if k!="m"})
    for name,row in result["rasid"]["decision_quality"].items():
        print("rasid",name,{k:round(v,3) if isinstance(v,float) else v for k,v in row.items()})
