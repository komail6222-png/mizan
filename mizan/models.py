"""Explicit Hugging Face import and offline, optional checkpoint experiments."""
import base64,hashlib,json,os,shutil,tempfile
from pathlib import Path
from .core.contract import require,digest,canonical,Refused
from .core.artifacts import Artifacts,DOMAIN

PINS={
 "sanad":{"repo":"intfloat/multilingual-e5-small","revision":"fd1525a9fd15316a2d503bf26ab031a61d056e98","weight_sha256":"1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477","files":["config.json","tokenizer.json","tokenizer_config.json","special_tokens_map.json","sentencepiece.bpe.model","model.safetensors"]},
 "basira":{"repo":"facebook/dinov2-small","revision":"ed25f3a31f01632728cabb09d1542f84ab7b0056","weight_sha256":"ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1","files":["config.json","model.safetensors"]},
 "rasid":{"repo":"amazon/chronos-bolt-tiny","revision":"a0e552de83495b5c28c14c71c374f3e33280b340","weight_sha256":"75068728d376d2bec670379eeef4bfb4d24c0cfe24d957451f8d19b447030a32","files":["config.json","model.safetensors"]},
}


def sha(path):
    with open(path,"rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()


def fetch(kind,root):
    """User-invoked import: get fixed Git revision, check Git blobs/LFS, then install atomically."""
    try:from huggingface_hub import HfApi,hf_hub_download
    except ImportError as exc:raise Refused("HF_DEPENDENCY","Install requirements-hf.txt to fetch optional checkpoints") from exc
    pin=PINS[kind];root=Path(root);root.mkdir(parents=True,exist_ok=True);dest=root/kind
    require(not dest.exists(),"MODEL_EXISTS","A model directory already exists; verify it before use")
    meta=HfApi().model_info(pin["repo"],revision=pin["revision"],files_metadata=True)
    require(meta.sha==pin["revision"],"REVISION","Hub returned a different commit")
    known={f.rfilename:f for f in meta.siblings};sources=[]
    with tempfile.TemporaryDirectory(dir=root,prefix=".import-") as tmp:
        stage=Path(tmp)/"verified";stage.mkdir()
        for name in pin["files"]:
            require(name in known,"MODEL_FILE","Required file absent from the pinned repository: "+name)
            src=hf_hub_download(pin["repo"],name,revision=pin["revision"]);dst=stage/name
            shutil.copyfile(src,dst);actual=sha(dst)
            if name=="model.safetensors":
                require(actual==pin["weight_sha256"],"WEIGHT_HASH","Weight digest mismatch")
            elif known[name].lfs is not None:
                require(actual==known[name].lfs.sha256,"HUB_HASH","Companion LFS file digest mismatch")
            else:
                blob=dst.read_bytes();header=f"blob {len(blob)}\0".encode()
                require(hashlib.sha1(header+blob,usedforsecurity=False).hexdigest()==known[name].blob_id,"HUB_HASH","Git blob identity mismatch")
            sources.append({"name":name,"sha256":actual})
        signer=Artifacts(root/".trust")
        manifest={"pin":pin,"files":sources}
        seal=signer.seal("local-hf","model-import",signer.put(manifest),kind+"@"+pin["revision"])
        (stage/"mizan-manifest.json").write_bytes(canonical({"manifest":manifest,"seal":seal}))
        os.rename(stage,dest)
    return dest


def verify_local(kind,root):
    d=Path(root)/kind;pin=PINS[kind]
    require((d/"mizan-manifest.json").exists(),"HF_OFFLINE_MISS","Verified checkpoint has not been imported")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    trust=Path(root)/".trust"/"keys"/"release.pub"
    require(trust.is_file() and not trust.is_symlink(),"HF_TRUST","Missing local import trust root")
    public=trust.read_bytes()
    envelope=json.loads((d/"mizan-manifest.json").read_bytes());manifest=envelope["manifest"];seal=envelope["seal"]
    body={"tenant":"local-hf","kind":"model-import","object":digest(manifest),"context":kind+"@"+pin["revision"],"schema":"1","key_id":digest(public)}
    require(seal["body"]==body,"MODEL_MANIFEST","Manifest differs from the signed import")
    try:Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode(seal["signature"],validate=True),DOMAIN+canonical(body))
    except (InvalidSignature,ValueError) as exc:raise Refused("BAD_SIGNATURE","Offline import signature mismatch") from exc
    require(manifest["pin"]==pin and [i["name"] for i in manifest["files"]]==pin["files"],"MODEL_MANIFEST","Unexpected model manifest")
    require({x.name for x in d.iterdir()}==set(pin["files"])|{"mizan-manifest.json"},"MODEL_FILES","Unlisted model files present")
    for row in manifest["files"]:
        path=d/row["name"]
        require(not path.is_symlink() and path.is_file() and sha(path)==row["sha256"],"MODEL_HASH","Cached file integrity failed")
    require(manifest["files"][-1]["sha256"]==pin["weight_sha256"],"WEIGHT_HASH","Unapproved checkpoint")
    return d


def experiment(kind,root):
    """Run the pinned model on public fixtures, separate from the deployed CPU calibration."""
    path=verify_local(kind,root)
    os.environ["HF_HUB_OFFLINE"]="1"
    try:
        import torch
        from transformers import AutoModel,AutoTokenizer
    except ImportError as exc:raise Refused("HF_DEPENDENCY","Install the optional model runtime") from exc
    from . import examples
    import numpy as np
    with torch.inference_mode():
        if kind=="sanad":
            model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
            tok=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
            docs=examples.procurement()["documents"]
            strings=["query: ما القدرة الظاهرية الاسمية لهذا الجهاز؟"]+["passage: "+s for d in docs for s in d["pages"][0].splitlines()[1:]]
            x=tok(strings,padding=True,truncation=True,max_length=256,return_tensors="pt")
            h=model(**x).last_hidden_state;mask=x.attention_mask.unsqueeze(-1)
            v=torch.nn.functional.normalize((h*mask).sum(1)/mask.sum(1).clamp_min(1),dim=-1)
            order=(v[1:]@v[0]).argsort(descending=True).tolist()
            answer={"ranked_excerpts":[strings[i+1][9:] for i in order],"scope":"retrieval experiment; rule verifier retains verdict authority"}
        elif kind=="basira":
            from PIL import Image
            from .basira.engine import fit,rank_decision,score,decode_image
            model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
            def fn(im):
                rgb=np.asarray(im.convert("RGB").resize((224,224),Image.Resampling.BICUBIC),dtype=np.float32)/255
                x=(rgb-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
                h=model(pixel_values=torch.from_numpy(x.transpose(2,0,1).copy())[None]).last_hidden_state[0,1:]
                return torch.nn.functional.normalize(h,dim=-1).reshape(16,16,-1).cpu().numpy()
            payload=examples.inspection();b,cal,rev,_,_=fit(payload["reference"],payload["calibration"],fn,PINS[kind]["revision"]+"/224-imagenet-v1")
            value,_=score(fn(decode_image(payload["query"]["png"])),b)
            answer={"score":value,"bank_digest":rev,**rank_decision(cal,value,".05")}
        else:
            try:from chronos import BaseChronosPipeline
            except ImportError as exc:raise Refused("HF_DEPENDENCY","Install chronos-forecasting") from exc
            model=BaseChronosPipeline.from_pretrained(str(path),device_map="cpu",local_files_only=True)
            rows=examples.electricity(42);context=torch.tensor([x["load"] for x in rows[:-24]],dtype=torch.float32)
            quantiles,mean=model.predict_quantiles(context,prediction_length=24,quantile_levels=[.1,.5,.9])
            y=np.array([x["load"] for x in rows[-24:]])
            q=quantiles.detach().cpu().numpy().reshape(24,3)
            answer={"quantiles":q.tolist(),"mae":float(np.mean(abs(q[:,1]-y))),
                    "seasonal_naive_mae":float(np.mean(abs(np.array([x["load"] for x in rows[-192:-168]])-y))),
                    "scope":"single illustrative held-out day"}
    return {"model":PINS[kind]["repo"],"revision":PINS[kind]["revision"],"result":answer}
