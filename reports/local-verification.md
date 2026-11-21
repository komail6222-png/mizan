# Local verification | MIZAN 3.0.0

21 November 2026 · An independent run of the release on a second machine, before any change to the code. It completes the checks the release environment could not execute: the HTTP API, the PDF import path and the full test suite without skips.

**Result: every check passed. No code was changed.** Raw logs for each step were kept outside the repository; the commands below reproduce them.

## Machine and software

| Item | Value |
|---|---|
| Hardware | Intel Core i7-13645HX, 20 logical CPUs, 7 GiB RAM visible to WSL |
| Host OS | Windows 11 Home, build 26200 |
| Linux | Ubuntu 26.04.1 LTS under WSL2 (kernel 6.18.58.1, glibc 2.43); project on the Linux filesystem (`~/projects/mizan`), not under `/mnt/c` |
| Python | CPython 3.12.16, installed with uv 0.13.9 (`requires-python` unchanged) |
| Pinned packages | NumPy 2.3.5 · SciPy 1.17.0 · Pillow 13.0.0 · cryptography 50.0.0 · FastAPI 0.141.1 · uvicorn 0.52.1 · httpx 0.28.1 · PyMuPDF 1.26.6 (all from `requirements.txt`) |
| SQLite | 3.53.1 (`python -m mizan doctor`: no `SQLITE_PATCH`) |
| Tools | mcp 2.3.0 · Playwright 1.65.2 with Chrome for Testing 159.0.8154.6 · Node 22.22.1 · git 2.53.0 |
| Input | `MIZAN_v3.0_Release.zip`, SHA-256 `0efd382e29eebb1a870262c3088d6dbc51f8145ec96dfe2b820430908c0ae563` |

## Results

| # | Check | Command | Result |
|---|---|---|---|
| 1 | File integrity | `sha256sum -c SHA256SUMS` | 110/110 files OK |
| 2 | Application tests | `python -m unittest discover -s tests -v` | **102/102 passed, 0 skipped** (20.9 s). The two HTTP tests in `tests/test_api.py` executed and passed. See `reports/application-tests.txt`. |
| 3a | SANAD | `python -m mizan run sanad examples/sanad.json --out /tmp/sanad.json`, then `verify`, then `explain --lang ar` | SUCCEEDED · verified · Arabic explanation rendered from the proof |
| 3b | BASIRA | same three commands with `basira` | SUCCEEDED · verified · explanation rendered |
| 3c | RASID | same three commands with `rasid` | SUCCEEDED · verified · explanation rendered. Counterfactual "risk weight 0": expected cost −1.93, CVaR +7.97, as stated in `docs/RELEASE_3.0.md` |
| 4a | Exhibit, pinned key | `python -m mizan verify reports/demo/demo_results.json --expected-key 9a627609771982875b75b6b28ff59091f40c5a116df11c1fffb94f37a51df436` | 7 results verified, `publisher_identity: pinned`. Negative control: a wrong expected key is refused (`KEY`, exit 2). |
| 4b | Reference checks | `python verification/reference_checks.py /tmp/ref.json` | 8/8 PASS; identical to `reports/reference_verification.json` apart from the environment fields |
| 5 | Evaluation reproduction | `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/evaluate.py --days 60 --out /tmp/eval` then `python scripts/compare_evaluations.py reports/evaluation /tmp/eval` | SANAD and BASIRA identical; RASID within the documented tolerance (details below). 42 s. |
| 6a | Safety suite, quick | `python scripts/safety_suite.py --quick --out /tmp/safety-quick.json` | 6,000 simulation steps, 1,162 SANAD and 1,500 BASIRA checks, 40/40 plans, 320/320 mutations rejected; 0 violations; mutants 2/2 and 4/4 caught |
| 6b | Safety suite, full | `python scripts/safety_suite.py --out /tmp/safety-full.json` | Summary **identical** to `reports/safety/safety.json`: 40,000 steps, 4,653 SANAD checks, 9,000 BASIRA checks (max \|z\| 1.59), 200/200 plans, 1,600/1,600 mutations rejected, 0 violations |
| 6c | Exhibit logic under Node | `node scripts/check_exhibit_logic.mjs` | PASS: 7/7 receipts verified in page, tampered copy rejected, 24 RASID intervals |
| 6d | Browser | `python scripts/check_exhibits.py --chromium <Playwright Chrome> --out /tmp/browser.json` | At 1280 px and 390 px: 0 page errors, 0 external requests, 7/7 receipts verified in the browser, tampered copy rejected; evaluation report renders with 4 charts answering hover |
| 7a | HTTP API | `python -m mizan serve --port 7861`, then requests with the token from `var/mizan/operator.token` | See below. All expected status codes; the job result verifies offline. |
| 7b | PDF import | PyMuPDF-generated PDF → `scripts/import_inputs.py sanad` → `approve-document` → `run` → `verify` → `explain` | See below. REVIEW before approval, MEETS for all four rules after. |
| 7c | MCP | Official `mcp` 2.3.0 client → `python -m mizan --data /tmp/mcp-store mcp` | See below. `rasid_plan` then `verify_receipt` succeed; tampering and operator-only calls are refused. |

### 5. Evaluation reproduction in detail

| System | Reference (`reports/evaluation`) | This machine | Status |
|---|---|---|---|
| SANAD original set | 36/36, 0 false MEETS, 24 decisive | 36/36, 0 false MEETS, 24 decisive; rows identical | identical |
| SANAD grammar 2 extension | 16/16, 0 false MEETS, 9 decisive | same | identical |
| SANAD grammar 3 tables | 16/16, 8 decisive; row reader 2/16 with 2 false MEETS | same | identical |
| BASIRA frozen test | 40/40 defects, 8/40 intact flagged; bank digest and p-values | same; bank digest and every p-value identical | identical |
| BASIRA shipped bank | long-run false alerts ≈ 12.9%, probability of a bank this bad 3.7% | 0.1285, 0.0368 | identical |
| BASIRA calibration study | banks above 5%: 25.3% (m=24), 22.5% / PAC 4.3% (m=50), 30.0% / PAC 1.5% (m=100) | 0.2525, 0.225 / 0.0425, 0.30 / 0.015 | identical |
| RASID forecast | ridge MAE 2.794 kW vs seasonal 3.068 kW | 2.793685411438331 vs 2.793685411438333 (difference in the 15th digit); seasonal identical | equal to floating-point precision |
| RASID risk-neutral candidate, seasonal baseline, hindsight, no battery | total cost 16,383.52 / 16,401.90 / 16,252.80 / 17,266.07 | same | identical (≤ 1e-9) |
| RASID risk-weighted candidate | total 16,441.57; share of battery value 81.4%; worst-10%-day mean 321.12 | 16,441.88; 81.3%; 321.07 | differs by 0.31 (0.002%) on 22 of 60 days, from 2026-02-12 |
| Price of risk aversion (risk-weighted − risk-neutral) | +58.05; daily 95% CI [0.339, 1.649] | +58.36; daily 95% CI [0.338, 1.657] | conclusion unchanged: interval excludes zero |
| Forecast value (risk-neutral − seasonal) | −18.38; daily 95% CI [−0.712, 0.071] | −18.38; same interval | identical: not resolved |

**Why RASID moved.** Only the risk-weighted policy differs. The reference reports were produced with SciPy 1.18.1 (recorded in their `environment` block), while `requirements.txt` pins SciPy 1.17.0. The HiGHS solver inside SciPy can return a different schedule with an equally good objective; through the carried battery state and month-to-date peak, one such choice changes later days. This matches the earlier cross-environment record in `reports/reproduction-2.2.json` (0.016% there, 0.002% here). Every one of the 300 plans passed the independent verifier.

### 7a. HTTP API in detail

| Request | Expected | Observed |
|---|---|---|
| `GET /healthz` | 200 | 200 `{"status":"ok","profile":"local-cpu"}` |
| `GET /readyz` | ready, runner healthy | 200, `ready: true`, three subjects READY, runner alive with 0 consecutive errors |
| `GET /docs` | Swagger UI | 200, Swagger UI page |
| `POST /api/v1/jobs/sanad` without token / with a wrong token | 401 | 401 `UNAUTHORIZED` both times |
| `POST /api/v1/jobs/nothing` | 404 | 404 `KIND` |
| `POST /api/v1/jobs/sanad` with token and `Idempotency-Key` | 202 + job | 202, job id and status URL |
| Same key, same payload | same job | same job id |
| Same key, different payload | 409 | 409 `IDEMPOTENCY_CONFLICT` |
| Poll status URL | SUCCEEDED | SUCCEEDED |
| `GET /api/v1/metrics` | counters | 200 with HTTP, job and WAL counters |
| `python -m mizan verify` on the job result | verified | verified; result digest `4b998aa5…` equals the CLI run of the same input |

The operator token file was created with mode 600. The server was stopped before the next CLI command.

### 7b. PDF import in detail

1. A one-page text PDF was generated with PyMuPDF: a title line `UPS Technical Schedule` followed by the SANAD English schedule for model Atlas-20 (efficiency set to 96 %).
2. `python scripts/import_inputs.py sanad --model Atlas-20 --rules rules.json atlas-20-schedule.pdf --out payload.json` extracted the page text exactly and recorded the original file's SHA-256.
3. `run` before approval: every rule REVIEW (the document has no authority yet).
4. `approve-document review-document-1.json` listed one line, the title, as `UNCOVERED_CONTEXT` and acknowledgeable.
5. `approve-document review-document-1.json --level 2 --acknowledge-eligible`: authority 2, 1 acknowledged line, signed profile revision 2.
6. `run` after approval: MEETS for apparent power, runtime, efficiency and transfer time. `verify` passed. `explain --lang ar` quoted each evidence line with page and character span and reported the acknowledged line.

A separate data directory (`--data /tmp/pdf-store`) was used so the approval did not touch the default store.

### 7c. MCP in detail

| Step | Observed |
|---|---|
| `initialize` | server `mizan` 3.0.0, protocol 2025-11-25 |
| `list_tools` | `sanad_review_bid`, `basira_inspect`, `rasid_plan`, `explain_job`, `verify_receipt`, `audit_status` |
| `rasid_plan` (two-hour oracle) | VERIFIED_FEASIBLE, expected cost 2, charge [1, 0] kW, discharge [0, 1] kW |
| `verify_receipt` | verified (`kind: rasid`); the same receipt also verifies with `python -m mizan verify` |
| `verify_receipt` on a copy with one number changed | refused: `RECEIPT`, "Result or execution context changed" |
| call `approve_document` | refused: "operator-only actions are not exposed to agents" |
| `audit_status` | audit chain verified |

## Differences from the documented procedure

- `scripts/safety_suite.py` writes to `reports/safety/safety.json` by default. It was run with `--out /tmp/...` so the bundled evidence was not overwritten; the full run's summary was then compared with the bundled file.
- `scripts/check_exhibits.py` looks for a `chromium` or `google-chrome` executable on `PATH`. Playwright's browser was passed explicitly with `--chromium`.
- Packages were installed into the virtual environment with `uv pip install`, which resolves the same pinned versions as `pip install -r requirements.txt`.
- Each newly created store generates its own signing key, so receipts from these runs carry new key fingerprints. Only `reports/demo/demo_results.json` is expected to match the pinned key, and it does.

## What this run does not establish

- All inputs remain generated fixtures. This run reproduces the release's evidence; it does not extend it to real tenders, factories or tariffs.
- The witness, simulation and MCP server ran on one host, as in the release environment.
- The PDF path was exercised with a machine-generated text PDF, not a scanned or commercially laid-out document.
