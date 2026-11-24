"""Loaders over the verified Hugging Face checkpoints (optional runtime; skipped without it)."""
import os
import unittest

import numpy as np

from mizan import examples, models

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


if __name__ == "__main__":
    unittest.main()
