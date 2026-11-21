import contextlib
import io
from pathlib import Path
import tempfile
import time
import unittest

from mizan.examples import procurement

try:
    from fastapi.testclient import TestClient
    from mizan.api import app_factory
except ImportError as missing:  # The core runs without the HTTP extra; report the gap instead of an import error.
    TestClient = app_factory = None
    MISSING = str(missing)


@unittest.skipIf(TestClient is None, "FastAPI/httpx not installed: pip install -r requirements.txt")
class ApiTests(unittest.TestCase):
    def test_private_http_job_through_verified_worker(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), TestClient(app_factory(d, False)) as c:
            ready = c.get("/readyz").json()
            self.assertTrue(ready["ready"])
            self.assertTrue(ready["runner"]["alive"])
            path = "/api/v1/jobs/sanad"
            self.assertEqual(c.post(path, json={}).status_code, 401)
            headers = {"Authorization": "Bearer " + (Path(d) / "operator.token").read_text(), "Idempotency-Key": "demo"}
            body = procurement()
            first = c.post(path, json=body, headers=headers)
            self.assertEqual(first.status_code, 202)
            self.assertIn("x-request-id", first.headers)
            self.assertEqual(first.json(), c.post(path, json=body, headers=headers).json())
            self.assertEqual(c.post(path, json={"different": 1}, headers=headers).status_code, 409)
            until = time.monotonic() + 15
            while True:
                receipt = c.get(first.json()["status_url"], headers=headers).json()
                if receipt["state"] not in ("QUEUED", "RUNNING") or time.monotonic() > until:
                    break
                time.sleep(.05)
            self.assertEqual(receipt["state"], "SUCCEEDED")
            self.assertEqual(receipt["result"]["requirements"][0]["verdict"], "REVIEW")
            self.assertIn("signature", receipt["receipt"])
            self.assertEqual(c.get("/api/v1/jobs/missing", headers=headers).status_code, 404)
            self.assertEqual(c.post("/api/v1/jobs/unknown", json=body, headers=headers).status_code, 404)
            self.assertIn("provenance", receipt)
            self.assertEqual(c.get("/api/v1/metrics", headers=headers).json()["runner"]["consecutive_errors"], 0)
            self.assertEqual(c.get("/api/v1/metrics", headers=headers).json()["jobs"]["SUCCEEDED"], 1)

    def test_public_exhibit_does_not_open_a_store(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), TestClient(app_factory(d, True)) as c:
            self.assertEqual(c.get("/").status_code, 200)
            self.assertEqual(c.get("/healthz").json()["profile"], "public-exhibit")
            self.assertFalse(c.get("/readyz").json()["writes"])
            self.assertEqual(c.post("/api/v1/jobs/sanad", json={}).status_code, 403)
            self.assertEqual(list(Path(d).iterdir()), [])
