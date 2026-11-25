import contextlib
import csv
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mizan.basira import engine as b
from mizan.core.contract import Refused
from tests.visa_fixture import make_visa_tree


class SplitAndManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.csv = make_visa_tree(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_classes_match_types(self):
        from mizan.visa import CLASSES, TYPES
        self.assertEqual(len(CLASSES), 12)
        self.assertEqual(set(TYPES), set(CLASSES))
        self.assertEqual(set(TYPES.values()), {"complex structure", "multiple instances", "single instance"})

    def test_read_split_parses_and_refuses_train_anomalies(self):
        from mizan.visa import read_split
        with open(self.csv, newline="", encoding="utf-8") as f:
            raw = list(csv.reader(f))
        rows = read_split(self.csv)
        self.assertEqual(len(rows), len(raw) - 1)
        self.assertEqual(set(rows[0]), {"object", "split", "label", "image"})
        bad = Path(self.tmp.name) / "bad.csv"
        raw[-1][1] = "train"   # the last row is a test anomaly: mark it train
        with open(bad, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(raw)
        with self.assertRaises(Refused) as cm:
            read_split(bad)
        self.assertEqual(cm.exception.code, "VISA_SPLIT")

    def test_read_split_refuses_unsafe_paths(self):
        from mizan.visa import read_split
        with open(self.csv, newline="", encoding="utf-8") as f:
            raw = list(csv.reader(f))
        raw[1][3] = "../outside.JPG"
        bad = Path(self.tmp.name) / "bad.csv"
        with open(bad, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(raw)
        with self.assertRaises(Refused) as cm:
            read_split(bad)
        self.assertEqual(cm.exception.code, "VISA_SPLIT")

    def test_manifest_round_trip_and_tamper(self):
        from mizan.visa import build_manifest, read_split, verify_manifest
        rows = read_split(self.csv)
        manifest = build_manifest(self.root, rows, "a" * 64, "b" * 64)
        self.assertEqual([x["path"] for x in manifest["images"]], sorted(r["image"] for r in rows))
        verify_manifest(self.root, manifest)
        victim = manifest["images"][3]["path"]
        (self.root / victim).write_bytes((self.root / victim).read_bytes() + b"\0")
        with self.assertRaises(Refused) as cm:
            verify_manifest(self.root, manifest)
        self.assertEqual(cm.exception.code, "VISA_HASH")
        self.assertIn(victim, str(cm.exception))

    def test_engine_png_equals_engine_decode(self):
        from mizan.visa import engine_png
        path = self.root / "candle" / "Data" / "Images" / "Normal" / "0000.JPG"
        expected = Image.open(path).convert("L").resize((96, 96), Image.Resampling.BILINEAR)
        self.assertEqual(b.decode_image(engine_png(path)).tobytes(), expected.tobytes())


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        from mizan.visa import build_manifest, read_split
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.csv = make_visa_tree(self.root)
        self.manifest = build_manifest(self.root, read_split(self.csv), "a" * 64, "b" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def test_protocol_digest_is_sealed(self):
        from mizan.core.contract import digest
        from mizan.visa import PROTOCOL, SEALED_DIGEST
        self.assertEqual(digest(PROTOCOL), SEALED_DIGEST)

    def test_class_seed_known_value(self):
        import hashlib
        from mizan.visa import class_seed
        self.assertEqual(class_seed("candle"), int.from_bytes(hashlib.sha256(b"20220922:candle").digest()[:8], "big"))

    def test_split_is_deterministic_and_disjoint(self):
        from mizan.visa import protocol_split
        first = protocol_split(self.root, self.manifest, "candle", sizes=(2, 2))
        self.assertEqual(first, protocol_split(self.root, self.manifest, "candle", sizes=(2, 2)))
        lists = ("reference", "calibration", "draw_pool", "eval_pool", "test_normal", "test_anomaly")
        self.assertEqual([len(first[k]) for k in lists], [2, 2, 1, 1, 2, 1])
        seen = [p for k in lists for p in first[k]]
        self.assertEqual(len(seen), len(set(seen)))
        train = {x["path"] for x in self.manifest["images"] if x["object"] == "candle" and x["split"] == "train"}
        self.assertTrue(set(first["reference"] + first["calibration"] + first["draw_pool"] + first["eval_pool"]) <= train)
        self.assertEqual(first["duplicates_dropped"], 0)

    def test_duplicate_train_normals_are_dropped_and_counted(self):
        from mizan.visa import build_manifest, protocol_split, read_split
        source = self.root / "candle" / "Data" / "Images" / "Normal" / "0000.JPG"
        copy = "candle/Data/Images/Normal/0099.JPG"
        (self.root / copy).write_bytes(source.read_bytes())
        with open(self.csv, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["candle", "train", "normal", copy, ""])
        manifest = build_manifest(self.root, read_split(self.csv), "a" * 64, "b" * 64)
        split = protocol_split(self.root, manifest, "candle", sizes=(2, 2))
        self.assertEqual(split["duplicates_dropped"], 1)
        self.assertNotIn(copy, [p for k in ("reference", "calibration", "draw_pool", "eval_pool") for p in split[k]])

    def test_split_refuses_a_class_too_small_for_the_protocol(self):
        from mizan.visa import protocol_split
        with self.assertRaises(Refused) as cm:
            protocol_split(self.root, self.manifest, "candle")   # 6 train normals cannot hold 30 + 100
        self.assertEqual(cm.exception.code, "VISA_SPLIT_SIZE")


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        tree = base / "tree"
        self.csv = make_visa_tree(tree)
        self.archive = base / "VisA_test.tar"
        with tarfile.open(self.archive, "w") as t:
            for cls in ("candle", "pcb1"):
                t.add(tree / cls, arcname=f"VisA/{cls}")
        self.out = base / "out"
        self.manifest = base / "manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, archive_bytes):
        from scripts.fetch_visa import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--out", str(self.out), "--manifest", str(self.manifest),
                         "--archive-url", self.archive.as_uri(), "--split-url", self.csv.as_uri()],
                        archive_bytes=archive_bytes, split_bytes=self.csv.stat().st_size)

    def test_fetch_refuses_wrong_size_and_keeps_no_partial(self):
        code = self.fetch(self.archive.stat().st_size + 1)   # the download comes out one byte short
        self.assertNotEqual(code, 0)
        self.assertEqual(sorted(p.name for p in self.out.rglob("*") if p.suffix in (".part", ".tar")), [])
        self.assertFalse((self.out / "data").exists())
        self.assertFalse(self.manifest.exists())

    def test_fetch_writes_then_verifies_the_manifest(self):
        from mizan.visa import verify_manifest
        self.assertEqual(self.fetch(self.archive.stat().st_size), 0)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["images"]), 18)
        self.assertEqual(len(manifest["archive_sha256"]), 64)
        verify_manifest(self.out / "data" / "VisA", manifest)
        before = self.manifest.read_bytes()
        self.assertEqual(self.fetch(self.archive.stat().st_size), 0)   # a second run verifies, never rewrites
        self.assertEqual(self.manifest.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
