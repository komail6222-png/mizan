"""Offline UI verification. Optional dependency: Playwright with a Chromium executable.

Covers the exhibit and the Spaces, the evaluation report and its models page, and (with --site) the introduction:
behaviour, layout at 1280 and 390 px, zero external requests, focus kept on redrawn controls, and WCAG AA text
contrast on every page checked (the report pages in their light and dark palettes). Any failure stops the run."""
import argparse
import json
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

# WCAG 2.x AA text contrast, from computed styles: every rendered element with its own text (HTML colour, or SVG fill),
# composited over the background colours behind it down to the first opaque one; a gradient background is checked at
# each of its colour stops. Effective opacity applies; fully transparent text (hidden tooltips) and disabled controls
# (WCAG's inactive components) are not counted. Canvas text is not in the DOM and is not covered.
CONTRAST = r"""() => {
 const parse = c => { if (!c) return null; let m = c.match(/rgba?\(([^)]+)\)/);
  if (m) { const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; }
  m = c.match(/color\(srgb ([^)]+)\)/);
  if (m) { const p = m[1].split(/[ \/]+/).filter(Boolean).map(Number); return {r: 255 * p[0], g: 255 * p[1], b: 255 * p[2], a: p.length > 3 ? p[3] : 1}; }
  return null; };
 const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
 const lum = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
 const over = (f, b) => ({r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1});
 const ratio = (x, y) => { const a = lum(x), b = lum(y); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
 function backdrops(el) {
  const layers = [];
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
   const s = getComputedStyle(e), c = parse(s.backgroundColor), img = s.backgroundImage && s.backgroundImage !== 'none' ? s.backgroundImage : '';
   const stops = img ? (img.match(/(?:rgba?|color)\([^)]*\)/g) || []).map(parse).filter(Boolean) : [];
   if (img && !stops.length) return null;
   layers.push({c, stops});
   if (c && c.a >= 1 && !stops.length) break;
  }
  let outs = [{r: 255, g: 255, b: 255, a: 1}];
  for (let i = layers.length - 1; i >= 0; i--) {
   const {c, stops} = layers[i];
   outs = outs.map(b => c && c.a > 0 ? over(c, b) : b);
   if (stops.length) outs = outs.flatMap(b => stops.map(x => over(x, b)));
  }
  return outs;
 }
 const seen = new Set(), failures = []; let checked = 0, exempt = 0, unmeasured = 0;
 const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
 while (walker.nextNode()) {
  const node = walker.currentNode, el = node.parentElement;
  if (!node.textContent.trim() || !el || seen.has(el)) continue;
  seen.add(el);
  const s = getComputedStyle(el);
  if (s.visibility === 'hidden' || !el.getClientRects().length) continue;
  let opacity = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) opacity *= Number(getComputedStyle(e).opacity);
  if (opacity < 0.01) continue;
  if (el.closest('[disabled],[aria-disabled="true"]')) { exempt++; continue; }
  const svg = el.closest('svg'), fg = parse(svg ? s.fill : s.color), backs = backdrops(svg || el);
  if (!fg || !backs) { unmeasured++; continue; }
  const size = parseFloat(s.fontSize), weight = Number(s.fontWeight), need = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
  const worst = Math.min(...backs.map(b => ratio(over({...fg, a: fg.a * opacity}, b), b)));
  checked++;
  if (worst < need) failures.push({text: node.textContent.trim().slice(0, 50), ratio: Math.round(worst * 100) / 100, need, color: svg ? s.fill : s.color, size});
 }
 return {checked, exempt, unmeasured, failures};
}"""


def contrast(page, where):
    """Fail on any WCAG AA text-contrast violation on the page as rendered; return the counts for the record."""
    found = page.evaluate(CONTRAST)
    assert not found["failures"] and not found["unmeasured"], (where, found)
    return {"text_elements": found["checked"], "failures": 0, "disabled_exempt": found["exempt"]}


def add_contrast(total, part):
    for k in ("text_elements", "disabled_exempt"):
        total[k] = total.get(k, 0) + part[k]
    total["failures"] = 0
    return total


def find_browser(explicit=None):
    for name in [explicit, "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]:
        if name and (shutil.which(name) or Path(name).is_file()):
            return shutil.which(name) or name
    raise SystemExit("No Chromium-family browser found; pass --chromium PATH")


def check_landing(browser, site, screenshots=None):
    """The bilingual introduction: weighs every receipt in the browser, rejects a tampered copy, restores, switches to RTL Arabic."""
    results = []
    page_file = Path(site).resolve() / "index.html"
    for width, height in [(1280, 900), (390, 844)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors, external = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda req: external.append(req.url) if req.url.startswith(("https:", "http:")) else None)
        page.goto(page_file.as_uri() + "?lang=en")
        page.wait_for_function("document.querySelector('#instrument').classList.contains('level')", timeout=15000)
        assert "match their signatures" in page.inner_text("#scale-status")
        rows = page.locator("#ledger li.ok").count()
        page.wait_for_timeout(400)  # colour transitions settle before the contrast audit
        audit = contrast(page, f"landing-en {width}")
        page.locator("#tamper").click()
        page.wait_for_function("document.querySelector('#instrument').classList.contains('tipped')", timeout=10000)
        assert "Rejected" in page.inner_text("#scale-status") and page.locator("#ledger li.bad").count() == 1
        page.locator("#tamper").click()
        page.wait_for_function("document.querySelector('#instrument').classList.contains('level')", timeout=10000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "landing overflows"
        page.locator("#lang").click()
        assert page.evaluate("document.documentElement.dir") == "rtl" and "تطابق" in page.inner_text("#scale-status")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Arabic landing overflows"
        steps = page.locator("#rail button")
        steps.nth(1).click()
        assert steps.nth(1).get_attribute("aria-pressed") == "true"
        # The rail is redrawn on every choice; focus must stay on the chosen step, by mouse and by keyboard.
        focused = "(() => { const b = [...document.querySelectorAll('#rail button')]; return b.indexOf(document.activeElement); })()"
        assert page.evaluate(focused) == 1, "focus left the step that was clicked"
        steps.nth(3).focus()
        page.keyboard.press("Enter")
        assert page.evaluate(focused) == 3 and steps.nth(3).get_attribute("aria-pressed") == "true", "focus left the step chosen with Enter"
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Space")
        assert page.evaluate(focused) == 2 and steps.nth(2).get_attribute("aria-pressed") == "true", "focus left the step chosen with Space"
        models = page.locator('#models a[href="report/models.html"]')  # the comparison link lands on the models page
        assert models.count() == 1 and "id=\"hf-section\"" in (Path(site) / "report/models.html").read_text(encoding="utf-8")
        page.wait_for_timeout(400)
        audit = add_contrast(audit, contrast(page, f"landing-ar {width}"))
        if screenshots:
            page.screenshot(path=str(Path(screenshots) / f"{width}-landing-ar.png"), full_page=True)
        assert not errors and not external, (errors, external)
        results.append({"viewport": [width, height], "views": ["landing-en", "landing-tampered", "landing-ar"],
                        "page_errors": 0, "external_requests": 0, "receipts_weighed_in_browser": rows,
                        "tampered_copy_rejected": True, "rtl": True, "focus_kept_on_redraw": True, "contrast": audit})
        page.close()
    return results


def check_models(browser, path, screenshots=None):
    """The Hugging Face comparison page: renders offline without overflow, every Reading cell agrees with the sign of
    its stored 95% interval, and the comparison tables marked .fit fit a phone screen without scrolling."""
    rasid = json.loads((ROOT / "reports/hf/rasid.json").read_bytes())
    derived = {c["comparison"]: c for c in json.loads((ROOT / "reports/hf/rasid_intervals.json").read_bytes())["comparisons"]}
    sanad = json.loads((ROOT / "reports/hf/sanad.json").read_bytes())
    results = []
    for width, height in [(1280, 900), (390, 844)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors, external = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda req: external.append(req.url) if req.url.startswith(("https:", "http:")) else None)
        page.goto(Path(path).resolve().as_uri())
        page.locator("#hf-section").wait_for(state="visible", timeout=10000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "models page overflows"
        # Paired cost differences: each Reading follows the sign of the stored interval.
        pairs = rasid["model"]["paired_comparisons"]
        spans = [p["mean_daily_difference_95ci"][0] <= 0 <= p["mean_daily_difference_95ci"][1] for p in pairs]
        expected = ["Not resolved" if s else "Costlier" if p["mean_daily_difference_95ci"][0] > 0 else "Cheaper" for p, s in zip(pairs, spans)]
        readings = page.locator("#hf-paired tr td:last-child").all_inner_texts()
        assert len(readings) == len(pairs) and all(r.startswith(x) for r, x in zip(readings, expected)), (readings, expected)
        key = [s for p, s in zip(pairs, spans) if (p["policy"], p["reference"]) == ("chronos_risk_neutral", "candidate_risk_neutral")]
        assert key and ("not resolved" in page.inner_text("#hf-paired-sub")) == key[0], page.inner_text("#hf-paired-sub")
        # Forecast-accuracy differences: each Reading follows the sign of its derived interval, never a point estimate.
        accuracy = [derived["chronos_minus_ridge_" + k] for k in ("load_mae", "mean_pinball", "truncated_crps", "coverage_80_load")]
        want = ["Higher" if c["interval_95"][0] > 0 else "Lower" if c["interval_95"][1] < 0 else "Not resolved" for c in accuracy]
        got = page.locator("#hf-accuracy tr td:last-child").all_inner_texts()
        assert len(got) == len(want) and all(r.startswith(x) for r, x in zip(got, want)), (got, want)
        # SANAD recall@1 by datasheet language: both sides of every (label, datasheet) pair, as stored.
        cells = page.locator("#hf-sanad-lang tr").all_inner_texts()[1:]
        pct = lambda v: f"{100 * v:.1f}%"
        order = (("en", "en"), ("en", "ar"), ("ar", "ar"), ("ar", "en"))
        assert len(cells) == 4 and all(pct(sanad["baseline"]["metrics"][q][d]["recall_at_1"]) in c and pct(sanad["model"]["metrics"][q][d]["recall_at_1"]) in c
                                       for c, (q, d) in zip(cells, order)), cells
        assert page.locator("#hf-tail tr").count() == 5, "the risk-weighted tail rows are missing"
        fit = page.evaluate("[...document.querySelectorAll('#hf-section table.fit')].map(t => t.parentNode.scrollWidth <= t.parentNode.clientWidth)")
        assert fit and all(fit), "an HF comparison table scrolls"
        audit = contrast(page, f"models {width}")
        assert not errors and not external, (errors, external)
        if screenshots:
            page.screenshot(path=str(Path(screenshots) / f"{width}-models.png"), full_page=True)
        page.close()
        audit = add_contrast(audit, dark_contrast(browser, path, width, height, "#hf-section"))
        results.append({"viewport": [width, height], "views": ["models"], "page_errors": 0, "external_requests": 0,
                        "hf_paired_readings": len(readings), "hf_accuracy_readings": len(got), "fit_tables": len(fit),
                        "contrast": {**audit, "color_schemes": ["light", "dark"]}})
    return results


def dark_contrast(browser, path, width, height, ready):
    """The report pages also define a dark palette (prefers-color-scheme); audit it the same way."""
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="dark")
    page.goto(Path(path).resolve().as_uri())
    page.locator(ready).first.wait_for(state="visible", timeout=10000)
    assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
    audit = contrast(page, f"{Path(path).name} dark {width}")
    page.close()
    return audit


def run(chromium=None, screenshots=None, demo=None, spaces=None, site=None):
    demo = Path(demo or ROOT / "reports/demo/MIZAN_Demo.html").resolve()
    spaces = Path(spaces or ROOT / "spaces").resolve()
    reports = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=find_browser(chromium),
                                     args=["--no-sandbox", "--disable-dev-shm-usage"], headless=True)
        for width, height in [(1280, 900), (390, 844)]:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors, external = [], []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("request", lambda req: external.append(req.url) if req.url.startswith(("https:", "http:")) else None)
            page.goto(demo.as_uri())
            snapshots, audit = [], {}
            def snapshot(name):
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name + " overflows"
                snapshots.append(name)
                add_contrast(audit, contrast(page, f"{name} {width}"))
                if screenshots:
                    dest = Path(screenshots)
                    dest.mkdir(exist_ok=True, parents=True)
                    page.screenshot(path=str(dest / f"{width}-{name}.png"))
            # Every choice redraws the view; focus must stay on the control that was chosen, by mouse or keyboard.
            def focused(label):
                active = page.evaluate("[document.activeElement.tagName, document.activeElement.textContent, document.activeElement.getAttribute('aria-pressed')]")
                assert active[0] == "BUTTON" and active[1].startswith(label) and active[2] == "true", (label, active)
            assert page.locator("#view[aria-live]").count() == 0 and page.locator("#trust[aria-live=polite]").count() == 1, "only #trust is a live region"
            trust = page.locator("#trust")
            trust.get_by_text("Verified in this browser").wait_for(timeout=10000)
            assert "7 of 7 results" in trust.inner_text()
            trust.get_by_role("button", name="Try a tampered copy").click()
            trust.get_by_text("Tampered copy rejected").wait_for(timeout=10000)
            assert page.locator(".proof q").count() == 2
            snapshot("procurement")
            page.get_by_role("button", name="Prefer approved schedule").click()
            focused("Prefer approved schedule")
            assert page.locator(".proof q").count() == 1 and "20 kVA" in page.locator(".proof q").inner_text()
            page.locator("button.rule").nth(2).click()
            assert page.evaluate("document.activeElement === document.querySelectorAll('button.rule')[2]"), "focus left the requirement"
            assert "94 %" in page.locator(".proof q").inner_text()
            page.get_by_role("button", name="BASIRA", exact=True).focus()
            page.keyboard.press("Enter")
            focused("BASIRA")
            page.locator("img").first.wait_for()
            assert page.evaluate("[...document.images].every(i=>i.complete && i.naturalWidth === 96)")
            page.get_by_role("button", name="Intact ring").focus()
            page.keyboard.press("Space")
            focused("Intact ring")
            assert "false positive" in page.locator("#view").inner_text()
            snapshot("inspection")
            page.get_by_role("button", name="Only 4 calibration parts").click()
            assert "1 / 5 = 0.20" in page.locator("#view").inner_text()
            page.get_by_role("button", name="RASID", exact=True).click()
            a = page.locator(".kpis").inner_text()
            page.get_by_role("button", name="Risk-neutral", exact=True).click()
            assert page.locator(".kpis").inner_text() != a and page.locator("canvas").count() == 2
            page.get_by_text("Show 24 hourly decisions").click()
            assert page.locator("#view table tr").count() == 25
            snapshot("energy")
            page.get_by_text("Run evidence", exact=True).click()
            with page.expect_download() as event:
                page.get_by_role("button", name="Download run JSON").click()
            packet = json.loads(Path(event.value.path()).read_bytes())
            assert len(packet["run"]["receipts"]) == len(packet["results"]) == 7
            for kind in ["sanad", "basira", "rasid"]:
                page.goto((spaces / kind / "index.html").as_uri())
                assert page.locator("#tabs button").all_text_contents() == [kind.upper()]
                page.locator("#trust").get_by_text("Verified in this browser").wait_for(timeout=10000)
                assert len(page.locator("h1").inner_text()) > 3
                snapshot("space-" + kind)
            assert not errors and not external, (errors, external)
            reports.append({"viewport": [width, height], "views": snapshots, "page_errors": len(errors),
                            "external_requests": len(external), "exported_receipts": 7, "schedule_intervals": 24,
                            "receipts_verified_in_browser": 7, "tampered_copy_rejected": True,
                            "focus_kept_on_redraw": True, "contrast": audit})
            page.close()
        # Evaluation report: renders offline, no overflow, every chart answers hover with a value.
        report = ROOT / "reports/evaluation/index.html"
        if report.exists():
            for width, height in [(1280, 900), (390, 844)]:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors, external = [], []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.on("request", lambda req: external.append(req.url) if req.url.startswith(("https:", "http:")) else None)
                page.goto(report.as_uri())
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "report overflows"
                answered = []
                for chart in ("banks", "share", "tail", "paired"):
                    hit = page.locator(f"#{chart} .hit").first
                    hit.scroll_into_view_if_needed()
                    box = hit.bounding_box()
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    page.wait_for_timeout(50)
                    answered.append(bool(page.locator(f"#{chart} .tip").inner_text().strip()))
                assert all(answered) and not errors and not external, (answered, errors, external)
                models = ROOT / "reports/evaluation/models.html"
                if models.exists():  # the Hugging Face comparison has its own page, linked from the report
                    link = page.locator('#models-link a[href="models.html"]')
                    assert link.count() == 1 and link.is_visible(), "the report does not link to the models page"
                page.mouse.move(0, 0)
                page.wait_for_timeout(400)  # the last tooltip has faded out before the contrast audit
                audit = contrast(page, f"report {width}")
                if screenshots:
                    page.screenshot(path=str(Path(screenshots) / f"{width}-report.png"), full_page=True)
                page.close()
                audit = add_contrast(audit, dark_contrast(browser, report, width, height, "#paired svg"))
                reports.append({"viewport": [width, height], "views": ["evaluation-report"], "page_errors": 0,
                                "external_requests": 0, "charts_with_tooltips": len(answered),
                                "contrast": {**audit, "color_schemes": ["light", "dark"]}})
            if (ROOT / "reports/evaluation/models.html").exists():
                reports.extend(check_models(browser, ROOT / "reports/evaluation/models.html", screenshots))
        if site:
            reports.extend(check_landing(browser, site, screenshots))
        browser.close()
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium")
    parser.add_argument("--screenshots")
    parser.add_argument("--demo", help="Exhibit HTML to test (default: bundled demo)")
    parser.add_argument("--spaces", help="Directory with sanad/basira/rasid Space folders")
    parser.add_argument("--site", help="Built site directory (scripts/build_site.py) whose index.html introduction is also checked")
    parser.add_argument("--out", default="reports/browser-checks.json")
    args = parser.parse_args()
    Path(args.out).write_text(json.dumps(run(args.chromium, args.screenshots, args.demo, args.spaces, args.site), indent=2) + "\n")
