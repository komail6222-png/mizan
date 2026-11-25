"""A tiny generated stand-in laid out like VisA, for tests that must not touch the real dataset or the network."""
import csv
from pathlib import Path

import numpy as np
from PIL import Image

STAND_IN_CLASSES = ("candle", "pcb1")


def _photo(seed, anomaly=False):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(40, 216, size=(48, 64, 3), dtype=np.uint8)
    if anomaly:
        pixels[18:30, 24:40] = 0
    return Image.fromarray(pixels, "RGB")


def make_visa_tree(root, per_class=6, test_normals=2):
    """Write <root>/<class>/Data/Images/{Normal,Anomaly}/*.JPG for two VisA class names and <root>/1cls.csv.

    Each class has per_class train normals, test_normals test normals and one test anomaly. Returns the CSV path."""
    root = Path(root)
    rows = []
    for c, cls in enumerate(STAND_IN_CLASSES):
        normal = root / cls / "Data" / "Images" / "Normal"
        anomaly = root / cls / "Data" / "Images" / "Anomaly"
        normal.mkdir(parents=True, exist_ok=True)
        anomaly.mkdir(parents=True, exist_ok=True)
        for i in range(per_class + test_normals):
            name = f"{cls}/Data/Images/Normal/{i:04d}.JPG"
            _photo(1000 * c + i).save(root / name, "JPEG", quality=95)
            rows.append([cls, "train" if i < per_class else "test", "normal", name, ""])
        name = f"{cls}/Data/Images/Anomaly/000.JPG"
        _photo(1000 * c + 999, anomaly=True).save(root / name, "JPEG", quality=95)
        rows.append([cls, "test", "anomaly", name, f"{cls}/Data/Masks/Anomaly/000.png"])
    path = root / "1cls.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["object", "split", "label", "image", "mask"])
        writer.writerows(rows)
    return path
