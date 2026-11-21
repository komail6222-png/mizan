"""Prepare local evidence files, standard images or vintage CSVs for the normal job API."""
import argparse,base64,csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mizan.core.contract import canonical,digest,require
from mizan.rasid.forecast import local_day
from mizan.examples import settings

def doc(path):
    file=Path(path);raw=file.read_bytes();require(len(raw)<=8<<20,"SIZE","Document exceeds 8 MiB")
    if file.suffix.lower()==".pdf":
        import fitz
        with fitz.open(stream=raw,filetype="pdf") as pdf:
            require(not pdf.is_encrypted and 0<len(pdf)<=40,"PDF","Use a text PDF of 1–40 pages")
            pages=[p.get_text("text",sort=True) for p in pdf]
        require(all(p.strip() for p in pages),"OCR_REVIEW","A page lacks a usable text transcript")
    else:pages=[raw.decode("utf-8-sig")]
    return {"name":file.name,"pages":pages,"original_file_sha256":digest(raw)}

def picture(path):
    p=Path(path);data=p.read_bytes();require(len(data)<150000,"SIZE","Image exceeds 150 KB")
    # Stems must identify independent physical capture groups; users must assign these explicitly.
    return {"group":p.stem,"png":base64.b64encode(data).decode()}

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="kind",required=True)
    s=sub.add_parser("sanad");s.add_argument("--model",required=True);s.add_argument("--rules",required=True);s.add_argument("documents",nargs="+")
    v=sub.add_parser("basira");v.add_argument("--reference",required=True);v.add_argument("--calibration",required=True);v.add_argument("--query",required=True)
    e=sub.add_parser("rasid");e.add_argument("--csv",required=True);e.add_argument("--day",required=True);e.add_argument("--timezone",default="UTC");e.add_argument("--planning")
    for subp in [s,v,e]:subp.add_argument("--out",required=True)
    a=parser.parse_args()
    if a.kind=="sanad":
        docs=[doc(p) for p in a.documents];payload={"model":a.model,"documents":docs,"rules":json.loads(Path(a.rules).read_bytes()),"compare_policies":True}
        for i,d in enumerate(docs):Path(a.out).with_name("review-document-"+str(i+1)+".json").write_bytes(canonical(d))
    elif a.kind=="basira":
        def folder(p):return [picture(f) for f in sorted(Path(p).iterdir()) if f.suffix.lower() in (".png",".jpeg",".jpg")]
        payload={"reference":folder(a.reference),"calibration":folder(a.calibration),"query":picture(a.query),"alpha":".05"}
    else:
        with open(a.csv,encoding="utf-8-sig",newline="") as f:rows=[{"time":x["time"],"known_at":x["known_at"],"load":float(x["load_kw"]),"pv":float(x["pv_kw"])} for x in csv.DictReader(f)]
        planning=json.loads(Path(a.planning).read_bytes()) if a.planning else settings(len(local_day(a.day,a.timezone)))
        payload={"history":rows,"day":a.day,"timezone":a.timezone,"planning":planning,"scenario_count":6}
    Path(a.out).write_bytes(canonical(payload))

if __name__=="__main__":main()
