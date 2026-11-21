"""Build the GitHub Pages site in _site/ from the signed exhibit, without re-running or re-signing anything.

python scripts/build_site.py [--out _site] [--expected-key FINGERPRINT]

Layout:
  index.html          the combined exhibit (reports/demo/MIZAN_Demo.html), which verifies itself in the browser
  report/index.html   the evaluation report (reports/evaluation/index.html)
  sanad/, basira/, rasid/   the three product Spaces (spaces/*/index.html)

Before copying, the script refuses to build unless:
  1. every signed result in reports/demo/demo_results.json verifies against the pinned key;
  2. the exhibit and the three Spaces are exactly the pages that scripts/build_exhibit.py and
     scripts/build_spaces.py generate from that signed packet;
  3. no page loads a script, stylesheet or image from another origin.
Pages are copied byte for byte.
"""
import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from mizan.core.contract import Refused, strict_load  # noqa: E402
from mizan.demos import exhibit_html  # noqa: E402
from mizan.receipts import verify_exhibit  # noqa: E402
from build_spaces import SPACES, build as build_spaces  # noqa: E402

PINNED_KEY = "9a627609771982875b75b6b28ff59091f40c5a116df11c1fffb94f37a51df436"
PACKET = ROOT / "reports/demo/demo_results.json"
PAGES = {
    "index.html": "reports/demo/MIZAN_Demo.html",
    "report/index.html": "reports/evaluation/index.html",
    **{f"{kind}/index.html": f"spaces/{kind}/index.html" for kind in SPACES},
}
EXTERNAL = re.compile(r"""(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", re.IGNORECASE)


def fail(message):
    raise SystemExit("build_site: " + message)


def check_sources(expected_key):
    packet = strict_load(PACKET.read_bytes())
    try:
        verified = verify_exhibit(packet, expected_key)
    except Refused as exc:
        fail(f"signed packet refused ({exc.code}): {exc}")
    exhibit = (ROOT / PAGES["index.html"]).read_text(encoding="utf-8")
    if exhibit_html(packet) != exhibit:
        fail("reports/demo/MIZAN_Demo.html is not the page generated from the signed packet")
    with tempfile.TemporaryDirectory() as tmp:
        build_spaces(PACKET, tmp)
        for kind in SPACES:
            expected = (Path(tmp) / kind / "index.html").read_text(encoding="utf-8")
            if expected != (ROOT / "spaces" / kind / "index.html").read_text(encoding="utf-8"):
                fail(f"spaces/{kind}/index.html is not the page generated from the signed packet")
    return verified, packet["run"]["key_fingerprint"]


def build(out, expected_key=PINNED_KEY):
    verified, fingerprint = check_sources(expected_key)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    for target, source in PAGES.items():
        data = (ROOT / source).read_bytes()
        if EXTERNAL.search(data.decode("utf-8")):
            fail(f"{source} references another origin")
        dest = out / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"{target:20} <- {source}  sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    (out / ".nojekyll").write_text("")
    print(f"{verified} signed results verified against key {fingerprint}; site written to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=ROOT / "_site")
    parser.add_argument("--expected-key", default=PINNED_KEY,
                        help="SHA-256 fingerprint of the publisher key (default: the pinned exhibit key)")
    args = parser.parse_args()
    build(args.out, args.expected_key)
