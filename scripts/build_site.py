"""Build the GitHub Pages site in _site/ from the signed exhibit, without re-running or re-signing anything.

python scripts/build_site.py [--out _site] [--expected-key FINGERPRINT]

Layout:
  index.html          the bilingual introduction (site/landing.html); it re-verifies the signed results itself
  exhibit/index.html  the combined exhibit (reports/demo/MIZAN_Demo.html)
  report/index.html   the evaluation report (reports/evaluation/index.html)
  report/models.html  the Hugging Face model comparison (reports/evaluation/models.html)
  sanad/, basira/, rasid/   the three product Spaces (spaces/*/index.html)
  og.png              the link-preview image (site/og.png)
  OFL-ReadexPro.txt   the SIL Open Font License of the embedded Readex Pro typeface

Before writing anything, the script refuses to build unless:
  1. every signed result in reports/demo/demo_results.json verifies against the pinned key;
  2. the exhibit and the three Spaces are exactly the pages that scripts/build_exhibit.py and
     scripts/build_spaces.py generate from that signed packet;
  3. every number on the introduction is read from reports/ (a missing value stops the build), the
     wording "resolved" / "not resolved" matches the sign of the reported 95% intervals (cost differences from
     the evaluation files, forecast-accuracy differences from reports/hf/rasid_intervals.json, which must have been
     derived from the evidence files as they are now), every comparison the Foundation models block states in
     words still holds in reports/hf/, and the test counts and where the skipped tests ran match
     reports/application-tests.txt;
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
    "report/models.html": "reports/evaluation/models.html",
    **{f"{kind}/index.html": f"spaces/{kind}/index.html" for kind in SPACES},
    "og.png": "site/og.png",
    "OFL-ReadexPro.txt": "mizan/web/fonts/OFL-ReadexPro.txt",  # the licence of the typeface every page embeds
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


def signed(v, places=2):
    return ("+" if v >= 0 else "−") + f"{abs(v):.{places}f}"


def percent(v):
    return str((Decimal(str(v)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%"


INTERVALS = "reports/hf/rasid_intervals.json"


def interval_keys(prefix, c):
    """Value, 95% interval and phrase keys for one derived paired comparison (scripts/derive_hf_intervals.py)."""
    lo, hi = c["interval_95"]
    resolved = lo > 0 or hi < 0
    if resolved != c["resolved"]:
        fail(f"{INTERVALS}: {c['comparison']} says resolved={c['resolved']} for the interval {lo} to {hi}")
    return {prefix: signed(c["mean_difference"], 3), prefix + "_lo": signed(lo, 3), prefix + "_hi": signed(hi, 3),
            prefix + "_phrase_en": "resolved" if resolved else "not resolved", prefix + "_phrase_ar": "مؤكد" if resolved else "غير مؤكد"}


def comparisons(intervals):
    return {c["comparison"]: c for c in intervals["comparisons"]}


def check_derived(intervals, root=ROOT):
    """The derived intervals must have been computed from the evidence files as they are now."""
    for path, sha in intervals["source"]["files"].items():
        if hashlib.sha256((Path(root) / path).read_bytes()).hexdigest() != sha:
            fail(f"{INTERVALS} was derived from another {path}; run python scripts/derive_hf_intervals.py")


def hf_facts(basira, rasid, sanad, intervals):
    """The Foundation models block, read from reports/hf/: each model's figure next to the current component's.

    The block's fixed wording is checked against the files here; a statement that no longer holds stops the build.
    Accuracy differences are stated only with their paired 95% intervals from reports/hf/rasid_intervals.json."""
    for name, r in (("basira", basira), ("rasid", rasid), ("sanad", sanad)):
        if not (r["gate"]["passed"] and r["repeat_run"]["identical"] and r["fixture_only"]):
            fail(f"reports/hf/{name}.json: the block publishes only runs on generated data whose gate passed and whose repeat run was identical")
    # BASIRA: the frozen test, then the repeated-calibration study the block calls "roughly equal" within its stated precision.
    side = {k: basira[k]["frozen_test"] for k in ("baseline", "model")}
    intact = {k: sum(row["condition"] == "intact" for row in t["rows"]) for k, t in side.items()}
    if side["model"]["detected"] != side["baseline"]["detected"] or intact["model"] != intact["baseline"]:
        fail("BASIRA: the block says both sides detect the same defects on the same frozen items")
    study = [{row["m"]: row["marginal_mean_false_alert"] for row in basira[k]["calibration_study"]["rows"]} for k in ("baseline", "model")]
    if not study[0] or study[0].keys() != study[1].keys():
        fail("BASIRA: the two calibration studies no longer cover the same calibration sizes")
    gap = max(abs(study[0][m] - study[1][m]) for m in study[0]) * 100
    stated = re.search(r"±([0-9.]+) percentage points", basira["baseline"]["calibration_study"]["scope"])
    if not stated:
        fail("BASIRA: the study's scope no longer states the precision the block's 'roughly equal' is measured against")
    precision = float(stated.group(1))
    if not gap <= precision:
        fail("BASIRA: the study's mean false-alert rates are no longer within its stated precision; the block calls them roughly equal")
    # RASID: each accuracy difference with its paired interval, then the forecast value and the risk-weighted plan.
    fb, fm = rasid["baseline"]["forecast"], rasid["model"]["forecast"]
    acc = comparisons(intervals)
    for key, a, b in (("load_mae", fm["chronos_mae_kw"], fb["ridge_mae_kw"]), ("mean_pinball", fm["chronos_mean_pinball"], fb["ridge_mean_pinball"]),
                      ("truncated_crps", fm["chronos_truncated_crps"], fb["ridge_truncated_crps"]),
                      ("coverage_80_load", fm["chronos_coverage_80_load"], fb["ridge_coverage_80_load"])):
        c = acc["chronos_minus_ridge_" + key]
        if abs(c["mean_a"] - a) > 1e-9 or abs(c["mean_b"] - b) > 1e-9:
            fail(f"RASID: {INTERVALS} was not derived from this reports/hf/rasid.json ({key})")
    if any(acc["chronos_minus_ridge_" + k]["resolved"] for k in ("mean_pinball", "truncated_crps")):
        fail("RASID: the block says the pinball-loss and CRPS differences are not resolved")
    coverage = acc["chronos_minus_ridge_coverage_80_load"]
    if not coverage["interval_95"][1] < 0:
        fail("RASID: the block says Chronos's 80% ranges cover the load in fewer hours, a resolved difference")
    pairs = {(p["policy"], p["reference"]): p for p in rasid["model"]["paired_comparisons"]}
    weighted = pairs[("chronos", "candidate")]
    if not weighted["mean_daily_difference_95ci"][0] > 0:
        fail("RASID: the block says the risk-weighted Chronos plan costs more than the ridge one, a resolved difference")
    value = pairs[("chronos_risk_neutral", "candidate_risk_neutral")]
    lo, hi = value["mean_daily_difference_95ci"]
    resolved = lo > 0 or hi < 0
    # SANAD: E5 behind on the engine's bilingual query (the ceiling), ahead with either label alone, and that gain is
    # cross-lingual: ahead on datasheets in the other language, behind on datasheets in the query's own language.
    r1 = {k: {kind: sanad[k]["metrics"][kind]["all"]["recall_at_1"] for kind in ("bilingual", "en", "ar")} for k in ("baseline", "model")}
    if not (r1["model"]["bilingual"] < r1["baseline"]["bilingual"] and r1["model"]["en"] > r1["baseline"]["en"] and r1["model"]["ar"] > r1["baseline"]["ar"]):
        fail("SANAD: the block says E5 ranks the claim line first less often with the bilingual query and more often with each label alone")
    by = {k: {(q, d): sanad[k]["metrics"][q][d]["recall_at_1"] for q in ("en", "ar") for d in ("en", "ar")} for k in ("baseline", "model")}
    if not all(by["model"][q, q] < by["baseline"][q, q] for q in ("en", "ar")):
        fail("SANAD: the block says E5 ranks the claim line first less often on datasheets in the query's own language")
    if not all(by["model"][q, d] > by["baseline"][q, d] for q, d in (("en", "ar"), ("ar", "en"))):
        fail("SANAD: the block says E5's one-label gain comes from datasheets in the other language")
    return {
        "hf_basira_false": str(side["model"]["false_alerts"]),
        "hf_basira_false_base": str(side["baseline"]["false_alerts"]),
        "hf_basira_intact": str(intact["model"]),
        "hf_basira_detected": str(side["model"]["detected"]),
        "hf_basira_defects": str(side["model"]["test_items"] - intact["model"]),
        "hf_basira_m": str(side["model"]["calibration_m"]),
        "hf_basira_draws": group(basira["settings"]["draws"]),
        "hf_rasid_mae": f"{fm['chronos_mae_kw']:.3f}",
        "hf_rasid_mae_ridge": f"{fb['ridge_mae_kw']:.3f}",
        **interval_keys("hf_rasid_mae_gap", acc["chronos_minus_ridge_load_mae"]),
        "hf_rasid_cov": percent(coverage["mean_a"]),
        "hf_rasid_cov_ridge": percent(coverage["mean_b"]),
        "hf_rasid_days": str(rasid["model"]["target_days"]),
        "hf_rasid_value": signed(value["mean_daily_difference"], 3),
        "hf_rasid_lo": signed(lo, 3),
        "hf_rasid_hi": signed(hi, 3),
        "hf_rasid_phrase_en": "resolved" if resolved else "not resolved",
        "hf_rasid_phrase_ar": "مؤكد" if resolved else "غير مؤكد",
        "hf_rasid_weighted": signed(weighted["mean_daily_difference"], 3),
        "hf_rasid_weighted_lo": signed(weighted["mean_daily_difference_95ci"][0], 3),
        "hf_rasid_weighted_hi": signed(weighted["mean_daily_difference_95ci"][1], 3),
        "hf_sanad_lexical_r1": percent(r1["baseline"]["bilingual"]),
        "hf_sanad_e5_r1": percent(r1["model"]["bilingual"]),
        "hf_sanad_lexical_r1_en": percent(r1["baseline"]["en"]),
        "hf_sanad_e5_r1_en": percent(r1["model"]["en"]),
        "hf_sanad_lexical_r1_ar": percent(r1["baseline"]["ar"]),
        "hf_sanad_e5_r1_ar": percent(r1["model"]["ar"]),
        # recall@1 by (query label, datasheet language)
        **{f"hf_sanad_{side}_r1_{q}_{d}": percent(by[k][q, d]) for side, k in (("lexical", "baseline"), ("e5", "model"))
           for q in ("en", "ar") for d in ("en", "ar")},
    }


def recorded_tests(text):
    """The test counts the introduction states, from reports/application-tests.txt. It says that the main run passed,
    that every test it skipped is a Hugging Face loader test, that those passed in the optional environment, and that
    the same suite passed on Python 3.13; a record that does not show all of this stops the build."""
    ran = re.findall(r"^Ran (\d+) tests in [0-9.]+s$", text, re.M)
    ok = re.findall(r"^OK(?: \(skipped=(\d+)\))?$", text, re.M)
    if len(ran) != 1 or len(ok) != 1:
        fail("reports/application-tests.txt does not record exactly one passing run")
    total, skipped = int(ran[0]), int(ok[0] or 0)
    loader_skips = len(re.findall(r" \.\.\. skipped 'optional HF runtime'$", text, re.M))
    hf = re.search(r"^# \.venv-hf result: Ran (\d+) tests in [0-9.]+s: OK\b", text, re.M)
    if not skipped or loader_skips != skipped or not hf or int(hf.group(1)) != skipped:
        fail("reports/application-tests.txt: the introduction says every skipped test is a model-loader test that "
             "passed in the optional Hugging Face environment")
    py313 = re.search(r"^# Python 3\.13\.\d+ result\b.*: Ran (\d+) tests in [0-9.]+s: OK \(skipped=(\d+)\)$", text, re.M)
    if not py313 or (int(py313.group(1)), int(py313.group(2))) != (total, skipped):
        fail("reports/application-tests.txt: the introduction says the same tests pass on Python 3.13")
    return {"tests": str(total), "tests_run": str(total - skipped), "tests_skipped": str(skipped), "tests_hf": hf.group(1)}


def facts(packet):
    """Every figure the introduction states, read from reports/. Missing keys raise KeyError and stop the build."""
    sanad = load("reports/evaluation/sanad.json")
    suites = [sanad, sanad["grammar_2_extension"], sanad["grammar_3_tables"]]
    basira = load("reports/evaluation/basira.json")
    rasid = load("reports/evaluation/rasid.json")
    safety = load("reports/safety/safety.json")["summary"]
    tests = recorded_tests((ROOT / "reports/application-tests.txt").read_text(encoding="utf-8"))
    pairs = {(p["policy"], p["reference"]): p for p in rasid["paired_comparisons"]}
    premium = pairs[("candidate", "candidate_risk_neutral")]
    forecast = pairs[("candidate_risk_neutral", "seasonal_naive")]
    lo, hi = premium["mean_daily_difference_95ci"]
    if not lo > 0:
        fail("the risk premium interval no longer excludes zero; the introduction calls it resolved")
    lo, hi = forecast["mean_daily_difference_95ci"]
    if not lo < 0 < hi:
        fail("the forecast-value interval no longer includes zero; the introduction calls it not resolved")
    intervals = load(INTERVALS)
    check_derived(intervals)
    mae_gap = comparisons(intervals)["ridge_minus_seasonal_naive_load_mae"]
    if abs(mae_gap["mean_a"] - rasid["ridge_mae_kw"]) > 1e-9 or abs(mae_gap["mean_b"] - rasid["seasonal_naive_mae_kw"]) > 1e-9:
        fail(f"{INTERVALS}: the ridge and seasonal-naive errors are not those of reports/evaluation/rasid.json")
    passed, total = (int(x) for x in safety["rasid_invalid_mutations_rejected"].split("/"))
    return {
        "receipts": str(len(packet["run"]["receipts"])),
        "sanad_false": str(sum(s["false_meets"] for s in suites)),
        "sanad_cases": str(sum(s["fixture_rows"] for s in suites)),
        "sim_steps": group(safety["simulation_steps"]),
        "sim_viol": str(safety["simulation_violations"]),
        "sim_mut": safety["simulation_mutants_caught"],
        "rasid_rejected": group(passed),
        "rasid_invalid": group(total),
        **tests,
        "basira_far": percent(basira["calibration_study"]["shipped_calibration"]["long_run_false_alert_estimate"]),
        "pac_min": str(basira["calibration_study"]["minimum_m_for_pac"]),
        "mae_ridge": f"{rasid['ridge_mae_kw']:.3f}",
        "mae_naive": f"{rasid['seasonal_naive_mae_kw']:.3f}",
        **interval_keys("mae_gap", mae_gap),
        "rasid_days": str(rasid["target_days"]),
        "premium": signed(premium["total_difference"]),
        "forecast_value": signed(forecast["total_difference"]),
        **hf_facts(*(load(f"reports/hf/{k}.json") for k in ("basira", "rasid", "sanad")), intervals),
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
