# Changelog

All notable changes to MIZAN. Detailed notes and evidence for each release are in `docs/`. Releases up to 3.0.0 are dated 21 November 2026; 3.1.0 is dated 25 November 2026.

## [3.1.0] - 2026-11-25

Foundation models measured on the held-out protocol, and the redesigned interface. Details: [`docs/RELEASE_3.1.md`](docs/RELEASE_3.1.md).

### Added
- **Hugging Face evaluation** (`scripts/evaluate_hf.py`, `mizan/hf_evaluation.py`, results in `reports/hf/`). DINOv2-small, Chronos-Bolt-tiny and multilingual-E5-small each replace one component, frozen, on the same held-out items, origins and metrics. A fidelity gate first re-runs the current component in the new harness (368, 253 and 30 checks, 0 mismatches), and each system was evaluated twice with identical results. BASIRA: 1/40 false alerts against 8/40 on the frozen test (one calibration bank), 40/40 defects for both; over 400 calibration banks per size the mean false-alert rates are roughly equal, as split conformal predicts for any descriptor. RASID: load MAE 2.572 against 2.794 kW, a paired daily difference of −0.221 kW (95% interval −0.698 to +0.118, not resolved), and the pinball-loss and CRPS differences are not resolved either; 80% coverage 65.1% against 74.7%, resolved; with the same risk-neutral planner −0.135 per day (95% interval −0.507 to +0.154, not resolved); the risk-weighted Chronos policy costs 1.060 more per day than the ridge one (interval excludes zero), a result the fixed scenario rule shapes as well as the forecaster. SANAD: recall@1 80.5% against 96.9% with the engine's bilingual query, where the lexical retriever is near its ceiling; 77.7% against 61.1% with the English label alone and 72.5% against 60.2% with the Arabic label alone, a gain that is entirely cross-lingual (in the query's own language E5 is lower: 85.8% against 100.0% in English, 77.5% against 94.0% in Arabic); no interval is reported for SANAD. The deployed engines are unchanged.
- **Derived accuracy intervals** (`scripts/derive_hf_intervals.py`, `reports/hf/rasid_intervals.json`): the shipped paired bootstrap applied, torch-free, to the per-day forecast errors and costs already in the evidence, with each input's SHA-256; a test recomputes the file byte for byte.
- Verified loaders for the three pinned checkpoints (`mizan/models.py`), which refuse an unverified import. The optional runtime is pinned in `requirements-hf.txt` (CPU-only torch) and stays out of `requirements.txt` and CI; `requirements-hf.lock.txt` lists the full package set of the environment that produced `reports/hf/`.
- The evaluation report links to a new models page (`reports/evaluation/models.html`, served as `report/models.html`) with every measure, the accuracy intervals, the risk-weighted plans' tail rows and SANAD recall@1 by datasheet language; the introduction gains a bilingual "Foundation models, measured honestly" block whose figures are read from `reports/hf/` and whose wording `scripts/build_site.py` checks against them, every accuracy direction with its interval, before building.
- **Bilingual introduction at the site root** (`site/landing.html`). A balance weighs the seven signed results while the browser recomputes each signature, and tips when one digit is changed. English and Arabic with right-to-left layout. Every figure on it is read from `reports/` by `scripts/build_site.py`, which also refuses to build if the wording about resolved and unresolved results no longer matches the reported intervals. The exhibit moved to `/exhibit/`.
- `scripts/check_exhibits.py --site` checks the introduction in Chromium (verification, tamper and restore, Arabic RTL, no overflow, zero external requests); the Pages workflow runs it before every deploy. It also audits WCAG AA text contrast on every page it opens (the report pages in light and dark) and checks that focus stays on redrawn controls.
- Tests: `test_hf_models.py`, `test_hf_evaluation.py`, `test_hf_intervals.py`, `test_site.py` (172 tests in total).

### Changed
- **Redesigned interface.** A new visual identity for the exhibit, the three Spaces and the local `mizan serve` page: an instrument-like layout, an embedded Readex Pro typeface (Arabic and Latin), and interactions built from data the old page did not show: an image wipe and per-patch readout for BASIRA, the calibration bank drawn against the alert threshold, linked hour-by-hour forecast and battery charts, the verifier's eleven constraint groups and the scenario costs for RASID. The signed data and the in-page verification code are unchanged; the pages were rebuilt from the same packet and still verify against the pinned key.
- The evaluation report (`scripts/build_report.py`) uses the same identity, with a Latin-only typeface subset so the page stays under its 60,000-character budget; its 3.0 figures are unchanged. Text colours meet WCAG AA contrast on every page, checked against the rendered pages (0 failures in 3,608 text-element checks, `reports/browser-checks.json`).
- `docs/TECHNICAL_GUIDE.md` no longer says that no reported number used the pinned checkpoints, and gives the full recipe for reproducing `reports/hf/`: the `.venv-hf` setup with torch from the CPU index, a check run into a scratch directory, the fields that differ by design, and the measured memory and disk use.
- Accessibility: focus stays on a redrawn control in the exhibit and on the introduction's step rail, only the verification result is a live region, and the focus ring is darker (#8a5f1c, and #ddb56b in the report's dark palette), at least 3:1 against every background it is drawn on.
- Each page names the embedded Readex Pro typeface and its OFL-1.1 licence; the licence file ships with each Space and at the site root.
- The Pages workflow pins Playwright and gives Pages write and OIDC access only to the deploy job; CI pins the MCP client.

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
