# Changelog

All notable changes to MIZAN. Detailed notes and evidence for each release are in `docs/`. Every release so far is dated 21 November 2026.

## [Unreleased]

### Changed
- **Redesigned interface.** A new visual identity for the exhibit, the three Spaces and the local `mizan serve` page: an instrument-like layout, an embedded Readex Pro typeface (Arabic and Latin), and interactions built from data the old page did not show: an image wipe and per-patch readout for BASIRA, the calibration bank drawn against the alert threshold, linked hour-by-hour forecast and battery charts, the verifier's eleven constraint groups and the scenario costs for RASID. The signed data and the in-page verification code are unchanged; the pages were rebuilt from the same packet and still verify against the pinned key.

### Added
- **Bilingual introduction at the site root** (`site/landing.html`). A balance weighs the seven signed results while the browser recomputes each signature, and tips when one digit is changed. English and Arabic with right-to-left layout. Every figure on it is read from `reports/` by `scripts/build_site.py`, which also refuses to build if the wording about resolved and unresolved results no longer matches the reported intervals. The exhibit moved to `/exhibit/`.
- The evaluation report (`scripts/build_report.py`) uses the same identity, with a Latin-only typeface subset so the page stays under its 60,000-character budget; its figures are unchanged. Text colours meet WCAG AA contrast on every page, checked against the rendered pages.
- `scripts/check_exhibits.py --site` checks the introduction in Chromium (verification, tamper and restore, Arabic RTL, no overflow, zero external requests); the Pages workflow runs it before every deploy.

## [3.0.0] - 2026-11-21

A trust layer for decisions in the age of AI agents. Details: [`docs/RELEASE_3.0.md`](docs/RELEASE_3.0.md).

### Added
- **MCP server** (`python -m mizan mcp`) with six tools: `sanad_review_bid`, `basira_inspect`, `rasid_plan`, `explain_job`, `verify_receipt`, `audit_status`. Agents have no approval, threshold or deployment tools, can read only their own jobs and have a per-session submission budget (default 25). A conformance test uses the official `mcp` 2.3.0 client.
- **Proof-only explanations** (`python -m mizan explain RESULT.json --lang ar`) in Arabic and English, rendered from the verified result's fields, with **executed counterfactuals** re-run through the same engine.
- **Self-verifying exhibit**: the bundled exhibit and the three Spaces recompute every SHA-256 digest and Ed25519 signature in the browser with WebCrypto. A "Try a tampered copy" button shows a forged result being rejected.
- **Safety suite** (`scripts/safety_suite.py`): deterministic simulation of the real broker (40,000 steps), SANAD metamorphic relations (4,653 checks), BASIRA property checks (9,000) and RASID verifier fuzzing (1,600 invalid mutations), with 0 violations; every deliberately broken version was caught.
- Tests: `test_agent.py`, `test_explain.py`, `test_safety.py` (102 tests in total).

### Repository and verification
- Independent verification on a second machine (`reports/local-verification.md`): all 102 tests passed, including the two HTTP tests the release environment skipped; the HTTP API, the PDF import path and an official-client MCP session were exercised for the first time. No code changes were needed.
- Packaging: `pyproject.toml` metadata, a `mizan` console script, Apache-2.0 license, and `requires-python` widened to `<3.14` after the test suite passed on Python 3.13.
- GitHub repository files: README, Arabic README, CI on Python 3.12 and 3.13, a GitHub Pages site built by `scripts/build_site.py`, `SECURITY.md`, `CONTRIBUTING.md`, `CITATION.cff`, `CLAUDE.md`.
- The release README moved to [`docs/TECHNICAL_GUIDE.md`](docs/TECHNICAL_GUIDE.md); `MIZAN_Final_Codex_Brief.md` moved to [`docs/history/`](docs/history/).

## [2.3.0] - 2026-11-21

Scientific corrections and new capabilities. Details: [`docs/RELEASE_2.3.md`](docs/RELEASE_2.3.md).

### Changed
- **BASIRA** explains the 20% false-alert observation: the shipped calibration bank is unlucky, with a long-run false-alert rate of about 12.9% (a bank this bad occurs 3.7% of the time). Every result reports the exact probability that its bank exceeds α.
- **RASID** separates forecast value from the price of risk aversion across five policies: the risk premium is +58.05 with a 95% interval excluding zero; the forecast's value under risk-neutral planning is −18.38, not resolved by 60 days.

### Added
- Optional **PAC rule** for BASIRA (`"guarantee": {"type": "pac", "delta": "0.1"}`), needing at least 45 calibration parts at α = 5%, δ = 10%, and a repeated-calibration study of 400 banks per size.
- **SANAD grammar 3**: multi-model datasheet tables with cell-level provenance (16/16, 0 false MEETS; a first-number row reader scores 2/16).
- **Audit witness**: a separate trust domain that countersigns the audit chain and reports WITNESSED, UNANCHORED_TAIL, DIVERGED or REGRESSED; `restore --accept-history-gap` records recovery gaps explicitly.
- Offline visual report `reports/evaluation/index.html` (`scripts/build_report.py`).

## [2.2.0] - 2026-11-21

Code review findings and changes. Details: [`docs/RELEASE_2.2.md`](docs/RELEASE_2.2.md).

### Fixed
- The background runner could stop silently on an unexpected exception while `/readyz` still reported ready. Job errors now fail only that job; runner health is reported.
- Status reads scanned the whole audit log under the global lock (27.6 ms per poll at 3,000 jobs); the signing generation is now stored on the job row (0.1 ms).
- Stopping the server mid-job marked the job failed permanently; it is now re-queued through restart fencing.
- Grammar 1 accepted digits from any Unicode script; only ASCII and Arabic-Indic digits remain.

### Added
- Offline verification of `run --out` files (`python -m mizan verify FILE [--expected-key FP]`).
- **SANAD grammar 2**: Arabic typography and units, and signed operator acknowledgement of non-evidential lines such as titles and footers.
- Numerical environment recorded in every evaluation report; `scripts/compare_evaluations.py`.

## [2.1.0] - 2026-11-21

Final build contract and release evidence for the local CPU release. Details: [`docs/FINAL_CHANGES.md`](docs/FINAL_CHANGES.md).

- The three decision systems (SANAD, BASIRA, RASID) running through one coordinator with fenced publication, signed results and an audit chain.
- Combined offline exhibit with seven signed demo results, three static Space directories and machine-readable evaluation reports.
