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


def model_identity(kind,root):
    """Identity of a verified checkpoint: pinned repository and revision, weight digest, manifest digest."""
    d=verify_local(kind,root);raw=(d/"mizan-manifest.json").read_bytes();manifest=json.loads(raw)["manifest"]
    weight={row["name"]:row["sha256"] for row in manifest["files"]}["model.safetensors"]
    require(weight==PINS[kind]["weight_sha256"],"WEIGHT_HASH","Unapproved checkpoint")
    return {"repo":manifest["pin"]["repo"],"revision":manifest["pin"]["revision"],"weight_sha256":weight,
            "manifest_sha256":hashlib.sha256(raw).hexdigest()}


def _torch():
    """Import torch with the fixed seed, deterministic kernels and thread count every loader shares."""
    try:import torch
    except ImportError as exc:raise Refused("HF_DEPENDENCY","Install the optional model runtime") from exc
    torch.manual_seed(0);torch.use_deterministic_algorithms(True,warn_only=True);torch.set_num_threads(8)
    return torch


def _auto(path,tokenizer=False):
    try:from transformers import AutoModel,AutoTokenizer
    except ImportError as exc:raise Refused("HF_DEPENDENCY","Install the optional model runtime") from exc
    model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
    return (model,AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)) if tokenizer else model


def dinov2_features(root):
    """DINOv2 patch grid for an image: bicubic 224, ImageNet normalisation, last hidden state without CLS,
    L2-normalised per patch; float64 array of shape (16, 16, 384)."""
    path=verify_local("basira",root);os.environ["HF_HUB_OFFLINE"]="1";torch=_torch()
    import numpy as np
    from PIL import Image
    model=_auto(path)
    mean=np.array([.485,.456,.406],np.float32);std=np.array([.229,.224,.225],np.float32)
    def features(image):
        rgb=np.asarray(image.convert("RGB").resize((224,224),Image.Resampling.BICUBIC),dtype=np.float32)/255
        x=torch.from_numpy(((rgb-mean)/std).transpose(2,0,1).copy())[None]
        with torch.inference_mode():
            h=model(pixel_values=x).last_hidden_state[0,1:]
            grid=torch.nn.functional.normalize(h,dim=-1).reshape(16,16,-1).cpu().numpy()
        return grid.astype(np.float64)
    return features


def chronos_forecaster(root,context_length=2048):
    """Chronos-Bolt quantiles for the next 24 hours of load and solar, forecast once per series from its
    trailing context (1-D, oldest first, last `context_length` values); shape (24, 2, len(levels)).
    Levels must be strictly increasing. The model's quantile heads can cross (seen on near-zero solar), so each
    hour's values are sorted along the levels: the monotone rearrangement, which keeps the set of values."""
    path=verify_local("rasid",root);os.environ["HF_HUB_OFFLINE"]="1";torch=_torch()
    import numpy as np
    try:from chronos import BaseChronosPipeline
    except ImportError as exc:raise Refused("HF_DEPENDENCY","Install chronos-forecasting") from exc
    pipeline=BaseChronosPipeline.from_pretrained(str(path),device_map="cpu",local_files_only=True)
    def forecast(load,solar,levels):
        levels=[float(x) for x in levels];out=[]
        require(levels and all(a<b for a,b in zip(levels,levels[1:])),"LEVELS","Quantile levels must be strictly increasing")
        for series in (load,solar):
            series=np.asarray(series,dtype=np.float64)
            require(series.ndim==1 and series.size>0,"CONTEXT","Each context must be a non-empty 1-D series")
            context=torch.tensor(series[-context_length:],dtype=torch.float32)
            with torch.inference_mode():
                quantiles,_=pipeline.predict_quantiles(context,prediction_length=24,quantile_levels=levels)
            out.append(quantiles[0].cpu().numpy())
        return np.sort(np.stack(out,axis=1),axis=-1).astype(np.float64)
    return forecast


def e5_embedder(root):
    """Multilingual E5 sentence vectors: mean pooling over the attention mask, L2-normalised; (n, 384) float64.
    Callers add the `query: ` and `passage: ` prefixes; all strings are encoded as one padded batch."""
    path=verify_local("sanad",root);os.environ["HF_HUB_OFFLINE"]="1";torch=_torch()
    import numpy as np
    model,tok=_auto(path,tokenizer=True)
    def embed(strings):
        x=tok(list(strings),padding=True,truncation=True,max_length=256,return_tensors="pt")
        with torch.inference_mode():
            h=model(**x).last_hidden_state;mask=x.attention_mask.unsqueeze(-1)
            v=torch.nn.functional.normalize((h*mask).sum(1)/mask.sum(1).clamp_min(1),dim=-1).cpu().numpy()
        return v.astype(np.float64)
    return embed


def experiment(kind,root):
    """Run the pinned model on public fixtures, separate from the deployed CPU calibration."""
    pin=PINS[kind]
    from . import examples
    import numpy as np
    if kind=="sanad":
        embed=e5_embedder(root)
        docs=examples.procurement()["documents"]
        strings=["query: ما القدرة الظاهرية الاسمية لهذا الجهاز؟"]+["passage: "+s for d in docs for s in d["pages"][0].splitlines()[1:]]
        v=embed(strings);order=np.argsort(-(v[1:]@v[0]),kind="stable").tolist()
        answer={"ranked_excerpts":[strings[i+1][9:] for i in order],"scope":"retrieval experiment; rule verifier retains verdict authority"}
    elif kind=="basira":
        from .basira.engine import fit,rank_decision,score,decode_image
        fn=dinov2_features(root)
        payload=examples.inspection();b,cal,rev,_,_=fit(payload["reference"],payload["calibration"],fn,pin["revision"]+"/224-imagenet-v1")
        value,_=score(fn(decode_image(payload["query"]["png"])),b)
        answer={"score":value,"bank_digest":rev,**rank_decision(cal,value,".05")}
    else:
        forecast=chronos_forecaster(root)
        rows=examples.electricity(42);history=rows[:-24]
        q=forecast(np.array([x["load"] for x in history]),np.array([x["pv"] for x in history]),[.1,.5,.9])[:,0,:]
        y=np.array([x["load"] for x in rows[-24:]])
        answer={"quantiles":q.tolist(),"mae":float(np.mean(abs(q[:,1]-y))),
                "seasonal_naive_mae":float(np.mean(abs(np.array([x["load"] for x in rows[-192:-168]])-y))),
                "scope":"single illustrative held-out day"}
    return {"model":pin["repo"],"revision":pin["revision"],"result":answer}
