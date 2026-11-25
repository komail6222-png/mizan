"""Loaders over the verified Hugging Face checkpoints (optional runtime; skipped without it), and the checks
before any loading, which run in the main torch-free suite."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from mizan import examples, models
from mizan.core.artifacts import Artifacts
from mizan.core.contract import Refused, canonical, digest

HF = os.environ.get("MIZAN_HF_MODELS")


def _has(mod):
    try: __import__(mod); return True
    except ImportError: return False


@unittest.skipUnless(HF and _has("torch") and _has("transformers") and _has("chronos"), "optional HF runtime")
class LoaderTests(unittest.TestCase):
    def test_dinov2_grid(self):
        f = models.dinov2_features(HF)(examples.part(9100, None))
        self.assertEqual(f.shape, (16, 16, 384)); self.assertTrue(np.allclose(np.linalg.norm(f, axis=-1), 1, atol=1e-5))

    def test_chronos_quantiles(self):
        load = np.linspace(40, 60, 24 * 14); pv = np.tile(np.r_[np.zeros(6), np.linspace(0, 20, 12), np.zeros(6)], 14)
        q = models.chronos_forecaster(HF)(load, pv, [.1, .5, .9])
        self.assertEqual(q.shape, (24, 2, 3)); self.assertTrue(np.all(np.diff(q, axis=-1) >= -1e-9))

    def test_e5_normalised(self):
        v = models.e5_embedder(HF)(["query: Transfer time", "passage: زمن التحويل: ٠ ms"])
        self.assertEqual(v.shape, (2, 384)); self.assertTrue(np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5))

    def test_identity_matches_pin(self):
        self.assertEqual(models.model_identity("basira", HF)["revision"], models.PINS["basira"]["revision"])


def _signed_import(root, kind, files):
    """A local import laid out and signed as models.fetch installs one (files in pin order, weights last), for a
    test pin made from the given file contents; returns the pin and the signed manifest object."""
    sha = lambda data: hashlib.sha256(data).hexdigest()
    pin = {"repo": "test/tiny", "revision": "0" * 40, "weight_sha256": sha(files["model.safetensors"]), "files": list(files)}
    d = Path(root) / kind; d.mkdir(parents=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    signer = Artifacts(Path(root) / ".trust")   # a new signing key for each root
    manifest = {"pin": pin, "files": [{"name": name, "sha256": sha(data)} for name, data in files.items()]}
    seal = signer.seal("local-hf", "model-import", signer.put(manifest), kind + "@" + pin["revision"])
    (d / "mizan-manifest.json").write_bytes(canonical({"manifest": manifest, "seal": seal}))
    return pin, manifest


class CheckpointVerificationTests(unittest.TestCase):
    """No torch: verification refuses before any model runtime is set up, and the identity names what is reproducible."""

    def test_loaders_and_identity_refuse_a_checkpoint_that_was_never_imported(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(models, "_torch", side_effect=AssertionError("the model runtime was set up before verification")):
            calls = {"dinov2_features": lambda: models.dinov2_features(d), "chronos_forecaster": lambda: models.chronos_forecaster(d),
                     "e5_embedder": lambda: models.e5_embedder(d),
                     **{f"model_identity({k})": (lambda k=k: models.model_identity(k, d)) for k in models.PINS}}
            for name, call in calls.items():
                with self.subTest(name), self.assertRaises(Refused) as caught:
                    call()
                self.assertEqual(caught.exception.code, "HF_OFFLINE_MISS")
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_identity_separates_the_signed_import_from_the_reproducible_manifest(self):
        files = {"config.json": b'{"tiny": true}', "model.safetensors": b"weights"}
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            pin, manifest = _signed_import(one, "basira", files); _signed_import(two, "basira", files)
            with mock.patch.dict(models.PINS, {"basira": pin}):
                first, second = models.model_identity("basira", one), models.model_identity("basira", two)
            self.assertEqual({k: first[k] for k in ("repo", "revision", "weight_sha256")}, {k: pin[k] for k in ("repo", "revision", "weight_sha256")})
            # manifest_sha256 is this machine's signed file; manifest_object is the same for every import of the pin.
            self.assertEqual(first["manifest_sha256"], hashlib.sha256((Path(one) / "basira" / "mizan-manifest.json").read_bytes()).hexdigest())
            self.assertNotEqual(first["manifest_sha256"], second["manifest_sha256"])
            self.assertEqual(first["manifest_object"], digest(manifest)); self.assertEqual(second["manifest_object"], first["manifest_object"])
            self.assertEqual(set(first), {"repo", "revision", "weight_sha256", "manifest_sha256", "manifest_object"})


if __name__ == "__main__":
    unittest.main()
