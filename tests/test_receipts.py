from pathlib import Path
import contextlib
import copy
import tempfile
import unittest
from mizan import examples
from mizan.core.contract import Refused, canonical, strict_load
from mizan.core.store import Broker
from mizan.receipts import verify_any, verify_job_output
from mizan.service import Runner, drain, initialize
from scripts.verify_exhibit import verify


class JobOutputTests(unittest.TestCase):
    """A `run --out` file must be checkable without the store that produced it."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d, contextlib.closing(Broker(d)) as b:
            initialize(b, "local")
            job = b.submit("local", "operator", "rasid", "oracle", examples.peak_oracle())
            # Round-trip through the exact bytes the CLI writes.
            cls.packet = strict_load(canonical(drain(b, Runner(b), "local", "operator", job)))
            cls.fingerprint = b.cas.key_id

    def test_fresh_output_verifies_offline_and_with_pinned_key(self):
        self.assertEqual(verify_job_output(self.packet)["kind"], "rasid")
        self.assertEqual(verify_any(self.packet, self.fingerprint)["publisher_identity"], "pinned")

    def test_tampering_and_wrong_trust_root_are_refused(self):
        mutations = [
            lambda p: p["result"]["certificate"].__setitem__("expected_cost", 0),
            lambda p: p.__setitem__("epoch", p["epoch"] + 1),
            lambda p: p["provenance"].__setitem__("input_digest", "0" * 64),
            lambda p: p["provenance"].__setitem__("generation", "other"),
            lambda p: p["receipt"].__setitem__("signature", p["receipt"]["signature"][::-1]),
            lambda p: p.__setitem__("kind", "sanad"),
            lambda p: p.pop("provenance"),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                altered = copy.deepcopy(self.packet)
                mutate(altered)
                with self.assertRaises(Refused):
                    verify_job_output(altered)
        with self.assertRaises(Refused):
            verify_job_output(self.packet, "f" * 64)


class ExhibitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.packet = strict_load((root / "reports/demo/demo_results.json").read_bytes())

    def test_all_public_results_have_signed_execution_context(self):
        self.assertEqual(verify(self.packet, self.packet["run"]["key_fingerprint"]), 7)

    def test_exhibit_key_pinning(self):
        with self.assertRaises(Refused):
            verify(self.packet, "0" * 64)

    def test_changed_result_and_replayed_epoch_fail_verification(self):
        for changed_result in (True, False):
            altered = copy.deepcopy(self.packet)
            if changed_result:
                altered["results"]["evidence"]["model"] = "Other"
            else:
                altered["run"]["receipts"][0]["epoch"] += 1
            with self.assertRaises(Refused):
                verify(altered)
