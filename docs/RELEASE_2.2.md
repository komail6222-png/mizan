# MIZAN 2.2 | Code review findings and changes

21 November 2026 · Review of the 2.1 local CPU release, followed by fixes and one product extension. Technical conflicts resolve in the order 2.2, 2.1, 2.0, then the original brief.

## 1. Baseline before any change

| Check | Result |
|---|---|
| Environment | Linux x86-64, 2 CPUs, Python 3.13.18, NumPy 2.5.3, SciPy 1.18.1, SQLite 3.53.1. No network: FastAPI, httpx and PyMuPDF could not be installed. The project declares Python 3.12; 3.13 was the available interpreter. |
| Unit tests | 30 of 31 modules' tests passed. `test_api` could not import FastAPI. |
| CLI | All three products and the two-hour oracle ran through the normal broker, worker and verifier. Each signed output verified. |
| Bundled exhibit | 7 receipts verified against the pinned fingerprint `9a627609…df436`. |
| Browser suite | `scripts/check_exhibits.py` passed in Chrome through Playwright at 1280 px and 390 px, with zero page errors and zero external requests. QUALIFICATION 2.1 listed this as outstanding. |

## 2. Findings

Each finding was reproduced with a probe before it was fixed. Severity reflects impact on the local release, not a production threat model.

| ID | Severity | Finding | Evidence | Change |
|---|---|---|---|---|
| R-1 | High | The background runner caught only a fixed tuple of exception types. Any other exception (for example `AttributeError`, `ArithmeticError`, `IndexError`, or `MemoryError` during the parent's replay) ended the runner thread. The server kept accepting jobs that never executed, and `/readyz` still reported ready. | Probe: the runner thread was dead one second after a job raised `AttributeError`. | A job exception now fails only that job with `WORKER_ERROR` and a structured log line. A fault outside a job, such as a store error during claim, is retried with bounded exponential backoff (max 5 s). Runner health is reported in `/readyz` and `/api/v1/metrics`. |
| R-2 | Medium | Every status read scanned the tenant's whole audit log, under the broker's global lock, to recover the generation that signed the result. The cost grew linearly and blocked the runner and all writers while it ran. | 27.6 ms per poll at 3,000 jobs. | The signing generation is stored on the job row (schema 2). An expand-only migration adds the column at startup and appends a `schema_migrated` audit event. Rows from 2.1 keep the audit-log fallback. Measured at 0.1 ms per poll on the same store. |
| R-3 | Medium | Stopping the server while a worker ran marked that job `FAILED` permanently, because `SHUTDOWN` was handled like a job error. | Code path in `Runner.one`. | The attempt stays `RUNNING`. On the next start, restart fencing re-queues it within the retry budget, and its old token remains fenced. |
| R-4 | Medium | A `run --out` file could not be verified away from the store. The signed context commits to the input digest, profile digest and generation, which the file did not contain. | The 2.1 verifier only handled exhibit packets. | Job reads now include a `provenance` block. `python -m mizan verify FILE [--expected-key FP]` checks job outputs and exhibit packets offline and opens no store. |
| S-1 | Medium | In SANAD grammar 1, any line outside the grammar anywhere in an approved document blocked every rule for that document. Titles, revision lines and page footers are common in real transcripts. Arabic tatweel inside a label also blocked matching. | A single title line `UPS Technical Schedule` turned every verdict into REVIEW. | Grammar 2, described in section 3. |
| S-2 | Low | Grammar 1 matched digits from any Unicode script through `\d`, for example Bengali `২০`, which falls outside the declared Arabic/English grammar. | Probe: `২০ kVA` was accepted as 20 kVA. | Only ASCII digits remain after the Arabic-Indic translation. Other scripts abstain. |
| L-1 | Low | When a job used up its retry budget on restart, no reason was recorded. On lease expiry it was labelled `EXPIRED`, the same code as a retryable expiry. | Code path in `Broker`. | `RESTART_FENCED`, `EXPIRED` and `RETRY_BUDGET_EXHAUSTED` are now distinct codes. |
| L-2 | Low | The CLI foreground loop spun without sleeping whenever it could not claim a job. | Code path in `__main__`. | `service.drain` sleeps briefly and enforces a 300-second foreground deadline. |
| L-3 | Low | The evaluation reports did not record NumPy or SciPy versions, so differences found on another machine could not be attributed to a library. | `reports/evaluation-log.txt` and `run-environment.json`. | Every evaluation report now includes an `environment` block. `scripts/compare_evaluations.py` compares two runs. |
| L-4 | Low | Without FastAPI installed, the test suite failed with an import error. | Baseline run. | The API tests now skip with an installation hint. Assertions for the new runner-health fields and the 404 for an unknown product were added. |

## 3. SANAD grammar 2

**Profile identity.** The engine is now `sanad/closed-ups-grammar/2`. Under invariant I-04 a changed parser needs a new identity. A store deployed with grammar 1 refuses SANAD jobs with `PROFILE_UPGRADE_REQUIRED`. `python -m mizan upgrade` performs an explicit signed promotion that keeps the existing source approvals, records its own audit event and makes no change when the engine is already current.

**Arabic coverage.** Tatweel (U+0640) is ignored. The Arabic thousands separator (U+066C) is accepted only in strict groups of three digits. The ASCII comma stays unsupported, because `20,000` and `20,5` are ambiguous across locales, which architecture §6.2 requires to remain REVIEW. Grammar 2 adds these unit spellings:

- Arabic kVA, VA, kW and W
- Arabic seconds and milliseconds
- English `sec`, `seconds`, `minute`, `msec` and `milliseconds`

Seconds still convert exactly, by rational division.

**Operator-acknowledged context.** During approval, the operator can declare individual uncovered lines non-evidential, such as a title or a footer. The approval records each line's page, span and the SHA-256 of its exact quote. It sits inside the signed profile, under the source identity, which already covers the transcript and the original-file digest. These safeguards apply:

- **Eligibility guard.** A line that names a scored attribute, or that contains a number followed by any scored unit (`%`, kVA, kW, min, s, ms and their spellings), can never be acknowledged. The CLI refuses it, and the evaluator checks eligibility again. A forged profile entry cannot unblock `Derated 10% above 40 °C` or `Output limited to 16 kVA in eco mode`.
- **Binding.** A changed quote, a shifted span or a different transcript does not match, and the line blocks again. Acknowledgements enter the evaluation digest, so the same documents under different approvals give distinguishable results.
- **Visibility.** Each requirement lists its `acknowledged_context`, and the result carries `acknowledged_lines`.
- **Boundary.** An acknowledgement is an assertion by the operator. A line such as `Values at 25 °C` uses no scored unit and is therefore eligible. Whether it qualifies the ratings is the reviewer's judgement, recorded with their approval. Header detection is never automatic.

`approve-document DOC` without `--level` only shows the review: every line the grammar will not turn into a claim, with its reason and eligibility. `--acknowledge PAGE:START` and `--acknowledge-eligible` record the selection together with `--level`.

## 4. Evidence

| Item | Result | Where |
|---|---|---|
| Unit and integration tests | 53 tests, of which 51 executed and passed. The 2 API tests were skipped because FastAPI was absent. New tests cover runner survival, shutdown semantics, retry-budget codes, O(1) lookup, 2.1→2.2 schema migration, offline verification with seven tamper cases, Arabic coverage, forged and stale acknowledgements, profile upgrade, and a CLI flow from approve through run to verify through the real worker subprocess. | `reports/application-tests.txt`, `tests/test_runtime.py`, `tests/test_sanad_v2.py`, `tests/test_receipts.py` |
| SANAD evaluation | The original 36 rows are identical, at 36/36 correct with zero false MEETS. The grammar-2 extension scores 16/16 with zero false MEETS and 9 decisive results; it includes four forged acknowledgements that must stay REVIEW. The extension fixtures were written together with the change, so they are regression evidence, not held-out evidence. | `reports/evaluation/sanad.json` |
| Cross-environment reproduction | SANAD rows are identical. The BASIRA bank digest and all 80 p-values are bit-identical. RASID forecasts are identical on all 60 days. On 2026-02-12 the newer HiGHS build returned a different optimum, which also passed the verifier. Because battery state and the monthly peak carry forward, the difference reached 21 later days. The candidate's total cost moved by −2.57 (0.016%), and it stays 39.67 above the seasonal baseline (42.24 in 2.1). The conclusion is unchanged. | `reports/reproduction-2.2.json`, `reports/reproduction-2.2/rasid-rerun.json` |
| Browser | The bundled exhibit and all three Spaces pass at desktop and mobile widths. A demo rebuilt with grammar 2 (`python -m mizan demo`) passes the same suite. | `reports/browser-checks.json`, `docs/screenshots/` |

## 5. Compatibility

- **Existing stores.** The schema migrates automatically on first open. SANAD then needs `python -m mizan upgrade` once. BASIRA and RASID engines are unchanged.
- **Backups.** The migration and the upgrade both append audit events. As before, `restore` accepts only a backup whose audit heads match the current store. Take a fresh backup after the first 2.2 start.
- **Bundled exhibit and Spaces.** These are unchanged on purpose. They are recorded 2.1 runs, and their signatures still verify against the pinned key. `python -m mizan demo` rebuilds them with grammar 2 under a new local key.
- **2.1 `run --out` files.** These have no provenance block. `verify` refuses them with `PROVENANCE`; rerun the job to get a verifiable file.
- **Approvals.** The 2.1 integer form (`"source": 2`) is still accepted. The new form is `{"level": 2, "acknowledged": [...]}`.

## 6. Not verified here, and still open

- The API edits compile but were not executed, because FastAPI was unavailable: runner health in `/readyz` and metrics, and the 404 for an unknown product. Run `python -m unittest discover -s tests -v` after `pip install -r requirements.txt`.
- The PDF import path was not exercised, because PyMuPDF was missing. The repository's declared Python 3.12 was not used here.
- The architecture gates listed in `docs/QUALIFICATION.md` that need a cluster, an accelerator, an external witness or real industrial data remain open. Release 2.2 does not change their status.
