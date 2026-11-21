"""Verify the result bytes and the job, epoch and deployment in each public receipt."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mizan.core.contract import strict_load
from mizan.receipts import verify_exhibit


def verify(packet, expected_key=None):
    return verify_exhibit(packet, expected_key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", nargs="?", default="reports/demo/demo_results.json")
    parser.add_argument("--expected-key", help="Previously trusted SHA-256 fingerprint of the signing public key")
    args = parser.parse_args()
    packet = strict_load(Path(args.packet).read_bytes())
    print(f"{verify(packet, args.expected_key)} signed results verified; key {packet['run']['key_fingerprint']}")
