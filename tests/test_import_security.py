import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mizan import models
from mizan.core.artifacts import Artifacts
from mizan.core.contract import canonical, Refused


class ImportSecurityTests(unittest.TestCase):
    def test_changed_offline_companion_and_rewritten_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkpoint = root / "fixture"
            checkpoint.mkdir()
            original = {"config.json": b'{"width":42}', "model.safetensors": b"test bytes for this unit check"}
            files = []
            for name, blob in original.items():
                (checkpoint / name).write_bytes(blob)
                files.append({"name": name, "sha256": hashlib.sha256(blob).hexdigest()})
            pin = {"revision": "a" * 40, "repo": "fixture/only", "files": list(original), "weight_sha256": files[-1]["sha256"]}
            manifest = {"pin": pin, "files": files}
            trust = Artifacts(root / ".trust")
            seal = trust.seal("local-hf", "model-import", trust.put(manifest), "fixture@" + pin["revision"])
            sidecar = checkpoint / "mizan-manifest.json"
            sidecar.write_bytes(canonical({"manifest": manifest, "seal": seal}))
            with patch.dict(models.PINS, fixture=pin):
                self.assertEqual(models.verify_local("fixture", root), checkpoint)
                config = checkpoint / "config.json"
                config.write_bytes(b'{"width":43}')
                with self.assertRaises(Refused):
                    models.verify_local("fixture", root)
                files[0]["sha256"] = models.sha(config)
                sidecar.write_bytes(canonical({"manifest": manifest, "seal": seal}))
                with self.assertRaises(Refused):
                    models.verify_local("fixture", root)
