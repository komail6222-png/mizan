"""Offline UI verification. Optional dependency: Playwright with a Chromium executable."""
import argparse
import json
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


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
        if screenshots:
            page.screenshot(path=str(Path(screenshots) / f"{width}-landing-ar.png"), full_page=True)
        assert not errors and not external, (errors, external)
        results.append({"viewport": [width, height], "views": ["landing-en", "landing-tampered", "landing-ar"],
                        "page_errors": 0, "external_requests": 0, "receipts_weighed_in_browser": rows,
                        "tampered_copy_rejected": True, "rtl": True})
        page.close()
    return results


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
            snapshots = []
            def snapshot(name):
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name + " overflows"
                snapshots.append(name)
                if screenshots:
                    dest = Path(screenshots)
                    dest.mkdir(exist_ok=True, parents=True)
                    page.screenshot(path=str(dest / f"{width}-{name}.png"))
            trust = page.locator("#trust")
            trust.get_by_text("Verified in this browser").wait_for(timeout=10000)
            assert "7 of 7 results" in trust.inner_text()
            trust.get_by_role("button", name="Try a tampered copy").click()
            trust.get_by_text("Tampered copy rejected").wait_for(timeout=10000)
            assert page.locator(".proof q").count() == 2
            snapshot("procurement")
            page.get_by_role("button", name="Prefer approved schedule").click()
            assert page.locator(".proof q").count() == 1 and "20 kVA" in page.locator(".proof q").inner_text()
            page.locator("button.rule").nth(2).click()
            assert "94 %" in page.locator(".proof q").inner_text()
            page.get_by_role("button", name="BASIRA", exact=True).click()
            page.locator("img").first.wait_for()
            assert page.evaluate("[...document.images].every(i=>i.complete && i.naturalWidth === 96)")
            page.get_by_role("button", name="Intact ring").click()
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
                            "receipts_verified_in_browser": 7, "tampered_copy_rejected": True})
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
                if screenshots:
                    page.screenshot(path=str(Path(screenshots) / f"{width}-report.png"), full_page=True)
                reports.append({"viewport": [width, height], "views": ["evaluation-report"], "page_errors": 0,
                                "external_requests": 0, "charts_with_tooltips": len(answered)})
                page.close()
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
