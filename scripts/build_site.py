"""Build the GitHub Pages site in _site/ from the signed exhibit, without re-running or re-signing anything.

python scripts/build_site.py [--out _site] [--expected-key FINGERPRINT]

Layout:
  index.html          the bilingual introduction (site/landing.html); it re-verifies the signed results itself
  exhibit/index.html  the combined exhibit (reports/demo/MIZAN_Demo.html)
  report/index.html   the evaluation report (reports/evaluation/index.html)
  sanad/, basira/, rasid/   the three product Spaces (spaces/*/index.html)
  og.png              the link-preview image (site/og.png)

Before writing anything, the script refuses to build unless:
  1. every signed result in reports/demo/demo_results.json verifies against the pinned key;
  2. the exhibit and the three Spaces are exactly the pages that scripts/build_exhibit.py and
     scripts/build_spaces.py generate from that signed packet;
  3. every number on the introduction is read from reports/ (a missing value stops the build), and the
     wording "resolved" / "not resolved" matches the sign of the reported 95% intervals;
  4. no page loads a script, stylesheet, font or image from another origin.
Exhibit pages are copied byte for byte. The introduction embeds the same signed packet and the exhibit's own
verification code, copied from mizan/web/template.html.
"""
import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP
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
TEMPLATE = ROOT / "mizan/web/template.html"
FONT = ROOT / "mizan/web/fonts/ReadexPro-subset.woff2"
LANDING = ROOT / "site/landing.html"
COPIED = {
    "exhibit/index.html": "reports/demo/MIZAN_Demo.html",
    "report/index.html": "reports/evaluation/index.html",
    **{f"{kind}/index.html": f"spaces/{kind}/index.html" for kind in SPACES},
    "og.png": "site/og.png",
}
# Anything that would make the browser fetch from another origin. Plain links (<a href>) are navigation, not loads.
EXTERNAL = [
    re.compile(r"""<(?:script|img|iframe|link|source|video|audio|embed|object|track)\b[^>]*\b(?:src|href|data|srcset)\s*=\s*["']?\s*(?:https?:)?//""", re.I),
    re.compile(r"""url\(\s*["']?\s*(?:https?:)?//""", re.I),
    re.compile(r"""@import\s""", re.I),
    re.compile(r"""<form\b[^>]*\baction\s*=\s*["']?\s*(?:https?:)?//""", re.I),
]


def fail(message):
    raise SystemExit("build_site: " + message)


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def check_sources(expected_key):
    packet = strict_load(PACKET.read_bytes())
    try:
        verified = verify_exhibit(packet, expected_key)
    except Refused as exc:
        fail(f"signed packet refused ({exc.code}): {exc}")
    exhibit = (ROOT / COPIED["exhibit/index.html"]).read_text(encoding="utf-8")
    if exhibit_html(packet) != exhibit:
        fail("reports/demo/MIZAN_Demo.html is not the page generated from the signed packet")
    with tempfile.TemporaryDirectory() as tmp:
        build_spaces(PACKET, tmp)
        for kind in SPACES:
            expected = (Path(tmp) / kind / "index.html").read_text(encoding="utf-8")
            if expected != (ROOT / "spaces" / kind / "index.html").read_text(encoding="utf-8"):
                fail(f"spaces/{kind}/index.html is not the page generated from the signed packet")
    return packet, verified


def group(n):
    return f"{n:,}"


def facts(packet):
    """Every figure the introduction states, read from reports/. Missing keys raise KeyError and stop the build."""
    sanad = load("reports/evaluation/sanad.json")
    suites = [sanad, sanad["grammar_2_extension"], sanad["grammar_3_tables"]]
    basira = load("reports/evaluation/basira.json")
    rasid = load("reports/evaluation/rasid.json")
    safety = load("reports/safety/safety.json")["summary"]
    tests_log = (ROOT / "reports/application-tests.txt").read_text(encoding="utf-8")
    ran = re.search(r"^Ran (\d+) tests", tests_log, re.M)
    if not ran or not re.search(r"^OK\b", tests_log, re.M):
        fail("reports/application-tests.txt does not record a passing run")
    pairs = {(p["policy"], p["reference"]): p for p in rasid["paired_comparisons"]}
    premium = pairs[("candidate", "candidate_risk_neutral")]
    forecast = pairs[("candidate_risk_neutral", "seasonal_naive")]
    lo, hi = premium["mean_daily_difference_95ci"]
    if not lo > 0:
        fail("the risk premium interval no longer excludes zero; the introduction calls it resolved")
    lo, hi = forecast["mean_daily_difference_95ci"]
    if not lo < 0 < hi:
        fail("the forecast-value interval no longer includes zero; the introduction calls it not resolved")
    far = Decimal(str(basira["calibration_study"]["shipped_calibration"]["long_run_false_alert_estimate"])) * 100
    passed, total = (int(x) for x in safety["rasid_invalid_mutations_rejected"].split("/"))
    signed = lambda v: ("+" if v >= 0 else "−") + f"{abs(v):.2f}"  # noqa: E731
    return {
        "receipts": str(len(packet["run"]["receipts"])),
        "sanad_false": str(sum(s["false_meets"] for s in suites)),
        "sanad_cases": str(sum(s["fixture_rows"] for s in suites)),
        "sim_steps": group(safety["simulation_steps"]),
        "sim_viol": str(safety["simulation_violations"]),
        "sim_mut": safety["simulation_mutants_caught"],
        "rasid_rejected": group(passed),
        "rasid_invalid": group(total),
        "tests": ran.group(1),
        "basira_far": str(far.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%",
        "pac_min": str(basira["calibration_study"]["minimum_m_for_pac"]),
        "mae_ridge": f"{rasid['ridge_mae_kw']:.3f}",
        "mae_naive": f"{rasid['seasonal_naive_mae_kw']:.3f}",
        "premium": signed(premium["total_difference"]),
        "forecast_value": signed(forecast["total_difference"]),
    }


def verification_code():
    text = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"/\*VERIFY:BEGIN\*/(.*?)/\*VERIFY:END\*/", text, re.S)
    if not match:
        fail("mizan/web/template.html has no VERIFY block")
    return match.group(1)


def landing_html(packet, figures):
    page = LANDING.read_text(encoding="utf-8")
    page = page.replace("%%FONT_WOFF2%%", base64.b64encode(FONT.read_bytes()).decode())
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(figures[m.group(1)]), page)
    page = page.replace("/*%%VERIFY%%*/", verification_code())
    if "{{" in page or "%%VERIFY%%" in page or "%%FONT" in page:
        fail("site/landing.html has an unfilled placeholder")
    # Same encoding as mizan.demos.exhibit_html, so the embedded packet is identical to the exhibit's.
    encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    if page.count("%%DEMO_DATA%%") != 1:
        fail("site/landing.html must embed the signed packet exactly once")
    return page.replace("%%DEMO_DATA%%", encoded), encoded


def check_external(name, text):
    for rule in EXTERNAL:
        hit = rule.search(text)
        if hit:
            fail(f"{name} loads from another origin: {hit.group(0)[:80]}")


def build(out, expected_key=PINNED_KEY):
    packet, verified = check_sources(expected_key)
    figures = facts(packet)
    landing, encoded = landing_html(packet, figures)
    if encoded not in (ROOT / COPIED["exhibit/index.html"]).read_text(encoding="utf-8"):
        fail("the introduction does not embed the same signed packet as the exhibit")
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    check_external("site/landing.html", landing)
    (out / "index.html").write_text(landing, encoding="utf-8")
    print(f"{'index.html':20} <- site/landing.html (generated)  sha256 {hashlib.sha256(landing.encode()).hexdigest()[:16]}")
    for target, source in COPIED.items():
        data = (ROOT / source).read_bytes()
        if target.endswith(".html"):
            check_external(source, data.decode("utf-8"))
        dest = out / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"{target:20} <- {source}  sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    (out / ".nojekyll").write_text("")
    print("figures: " + ", ".join(f"{k}={v}" for k, v in figures.items()))
    print(f"{verified} signed results verified against key {packet['run']['key_fingerprint']}; site written to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=ROOT / "_site")
    parser.add_argument("--expected-key", default=PINNED_KEY,
                        help="SHA-256 fingerprint of the publisher key (default: the pinned exhibit key)")
    args = parser.parse_args()
    build(args.out, args.expected_key)
