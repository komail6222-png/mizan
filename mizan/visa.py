"""VisA, the one real dataset in MIZAN: public images of 12 products with real defects.

Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and Segmentation",
arXiv:2207.14315, released under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). The images are never
committed: scripts/fetch_visa.py downloads them into var/visa and records every image's SHA-256 in
reports/visa/manifest.json, and evaluation refuses any image that does not match it.
"""
import csv
import hashlib
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image

from .basira import engine as b
from .core.contract import digest, require

CLASSES = ("candle", "capsules", "cashew", "chewinggum", "fryum", "macaroni1", "macaroni2",
           "pcb1", "pcb2", "pcb3", "pcb4", "pipe_fryum")
TYPES = {**{c: "complex structure" for c in ("pcb1", "pcb2", "pcb3", "pcb4")},
         **{c: "multiple instances" for c in ("capsules", "candle", "macaroni1", "macaroni2")},
         **{c: "single instance" for c in ("cashew", "chewinggum", "fryum", "pipe_fryum")}}

ARCHIVE_URL = "https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar"
ARCHIVE_BYTES = 1929840640
SPLIT_COMMIT = "2a692ab575001cbde74d402d897a7286086c6199"   # amazon-science/spot-diff main, 22 September 2022
SPLIT_URL = f"https://raw.githubusercontent.com/amazon-science/spot-diff/{SPLIT_COMMIT}/split_csv/1cls.csv"
SPLIT_BYTES = 660329
COLUMNS = ["object", "split", "label", "image", "mask"]
ATTRIBUTION = ('VisA (Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and '
               'Segmentation", arXiv:2207.14315), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Images were '
               'converted to 96×96 greyscale or resized for the model; no image is redistributed except the four 96 px '
               'examples shown.')


# The protocol, sealed before any VisA test image is scored (docs/superpowers/specs/2026-11-25-basira-visa-design.md).
# Changing any value changes digest(PROTOCOL); tests/test_visa.py pins it to SEALED_DIGEST, and a real run refuses
# a protocol whose digest differs. A later change is a new, separately labelled analysis.
PROTOCOL = {
    "dataset": {"name": "VisA", "archive_url": ARCHIVE_URL, "archive_bytes": ARCHIVE_BYTES,
                "split_url": SPLIT_URL, "split_commit": SPLIT_COMMIT, "split": "1cls", "licence": "CC BY 4.0"},
    "seed": 20220922,
    "class_seed": "the first 8 bytes, big-endian, of sha256('<seed>:<class>')",
    "split": {"reference": 30, "calibration": 100,
              "order": ("train normals sorted by path; a later image whose decoded 96 px greyscale image repeats an "
                        "earlier one is dropped and counted; then numpy.random.default_rng(class_seed).permutation"),
              "pools": "the rest halved: the first half is the draw pool, the second half the evaluation pool",
              "test": "the official test normals and test anomalies, untouched"},
    "configurations": {"as_shipped": {"reference": 10, "calibration": 24, "rules": ["marginal"]},
                       "full": {"reference": 30, "calibration": 100, "rules": ["marginal", "pac"]}},
    "alpha": "1/20",
    "delta": "1/10",
    "engine_path": "greyscale, bilinear resize to 96x96, PNG, submitted through basira.engine.decode_image",
    "scorers": {"descriptor": "the shipped hand-built descriptor on the engine path",
                "dinov2-engine": "models.dinov2_features on the engine path (the 96 px greyscale image)",
                "dinov2-native": "models.dinov2_features on the original colour image; off the engine path"},
    "statistics": {
        "rates": "Clopper-Pearson 95% interval",
        "exchangeability": ("the long-run false-alert rate of the rank-k-of-m threshold is Beta(m-k+1, k); per class "
                            "Beta-Binomial(n_test_normals, m-k+1, k), pooled by exact convolution; central 95% range "
                            "and two-sided tail probability"),
        "auroc": {"method": "Mann-Whitney, ties count 1/2", "bootstrap": "stratified by label", "draws": 2000, "seed": 2026},
        "paired": {"bootstrap": "stratified by class and label", "draws": 2000, "seed": 2026,
                   "measures": ["detection", "false_alert", "auroc"], "at": "full configuration, marginal rule",
                   "comparisons": [["dinov2-engine", "descriptor"], ["dinov2-native", "dinov2-engine"]]},
        "multiplicity": "no adjustment; stated on the pages"},
    "study": {"bank": "full", "draws": 400, "sizes": [24, 50, 100], "seed": 23, "cap": "m is capped by the draw pool",
              "false_alert_on": "evaluation pool", "detection_on": "official test anomalies"},
    "examples": {"scorer": "descriptor", "configuration": "full", "rule": "marginal", "order": "(class, path)",
                 "sets": ["caught", "missed", "false_alert", "passed"], "pick": "the lower median score of each set",
                 "format": "96x96 greyscale JPEG, quality 85"},
    "gate": {"fixture": "exact equality with the frozen test of reports/evaluation/basira.json",
             "dinov2": {"exact": ["calibration_m", "ranks", "p_values", "flags", "counts"],
                        "score_relative_tolerance": 1e-6, "exempt": ["bank_digest"]}},
}
SEALED_DIGEST = "2175caee24d3bd7102fb845560621cefd611f81a5ed9c11a6a6b5135370e705a"


def class_seed(cls):
    return int.from_bytes(hashlib.sha256(f"{PROTOCOL['seed']}:{cls}".encode()).digest()[:8], "big")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_relative(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def read_split(path):
    """The official split as rows {object, split, label, image}. Refuses (VISA_SPLIT) unknown columns, classes,
    splits or labels, a train anomaly, an unsafe or repeated image path."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames == COLUMNS, "VISA_SPLIT", f"Expected columns {COLUMNS}, found {reader.fieldnames}")
        for r in reader:
            where = f"{path}: {r.get('image')}"
            require(r["object"] in CLASSES, "VISA_SPLIT", f"Unknown class {r['object']!r} ({where})")
            require(r["split"] in ("train", "test") and r["label"] in ("normal", "anomaly"), "VISA_SPLIT",
                    f"Unknown split or label ({where})")
            require(not (r["split"] == "train" and r["label"] == "anomaly"), "VISA_SPLIT",
                    f"An anomaly in the train split breaks the one-class protocol ({where})")
            require(_safe_relative(r["image"]), "VISA_SPLIT", f"Unsafe image path ({where})")
            rows.append({k: r[k] for k in ("object", "split", "label", "image")})
    require(len({r["image"] for r in rows}) == len(rows), "VISA_SPLIT", "An image path is listed twice")
    return rows


def build_manifest(root, rows, archive_sha256, split_sha256):
    """Every listed image with its SHA-256, sorted by path, plus the identities of the archive and the split."""
    root = Path(root)
    images = []
    for r in sorted(rows, key=lambda r: r["image"]):
        p = root / r["image"]
        require(p.is_file(), "VISA_DATA", f"Listed image missing: {r['image']}")
        images.append({"path": r["image"], "sha256": sha256_file(p), "object": r["object"], "split": r["split"],
                       "label": r["label"]})
    return {"dataset": "VisA", "licence": "CC BY 4.0", "attribution": ATTRIBUTION,
            "archive_url": ARCHIVE_URL, "archive_bytes": ARCHIVE_BYTES, "archive_sha256": archive_sha256,
            "split_url": SPLIT_URL, "split_commit": SPLIT_COMMIT, "split_sha256": split_sha256, "images": images}


def verify_manifest(root, manifest):
    """Refuse (VISA_HASH) at the first listed image that is missing or whose bytes changed."""
    root = Path(root)
    for x in manifest["images"]:
        p = root / x["path"]
        require(p.is_file() and sha256_file(p) == x["sha256"], "VISA_HASH", f"Image missing or changed: {x['path']}")


def engine_png(path):
    """What the engine itself makes of the original: greyscale, then a bilinear resize to 96×96, encoded as PNG.
    Real photographs exceed the engine's input limits (1 MP, 150 KB), so a client submits exactly this."""
    with Image.open(path) as im:
        im.load()
        return b.encode_image(im.convert("L").resize((b.SIZE, b.SIZE), Image.Resampling.BILINEAR))


def protocol_split(root, manifest, cls, sizes=None):
    """One product line under the sealed protocol: reference, calibration, draw pool and evaluation pool from the train
    normals, and the untouched official test lists. `sizes` (reference, calibration) exists for tests only."""
    root = Path(root)
    n_ref, n_cal = sizes or (PROTOCOL["split"]["reference"], PROTOCOL["split"]["calibration"])
    images = [x for x in manifest["images"] if x["object"] == cls]
    train = sorted(x["path"] for x in images if x["split"] == "train" and x["label"] == "normal")
    kept, seen = [], set()
    for path in train:
        key = digest(b.decode_image(engine_png(root / path)).tobytes())
        if key not in seen:
            seen.add(key)
            kept.append(path)
    require(len(kept) >= n_ref + n_cal + 2, "VISA_SPLIT_SIZE",
            f"{cls}: {len(kept)} distinct train normals cannot hold {n_ref} reference, {n_cal} calibration and two pools")
    order = np.random.default_rng(class_seed(cls)).permutation(len(kept))
    shuffled = [kept[i] for i in order]
    rest = shuffled[n_ref + n_cal:]
    half = len(rest) // 2
    return {"class": cls, "seed": class_seed(cls), "duplicates_dropped": len(train) - len(kept),
            "reference": shuffled[:n_ref], "calibration": shuffled[n_ref:n_ref + n_cal],
            "draw_pool": rest[:half], "eval_pool": rest[half:],
            "test_normal": sorted(x["path"] for x in images if x["split"] == "test" and x["label"] == "normal"),
            "test_anomaly": sorted(x["path"] for x in images if x["split"] == "test" and x["label"] == "anomaly")}
