# MIZAN 2.3 | Scientific corrections and new capabilities

21 November 2026 · Builds on 2.2 (`docs/RELEASE_2.2.md`). Technical conflicts resolve in the order 2.3, 2.2, 2.1, 2.0, then the original brief. The visual summary is `reports/evaluation/index.html`, rebuilt with `python scripts/build_report.py`.

Release 2.3 began with two questions about results that 2.1 had reported as facts. Both answers changed how those results should be read. Both questions became new capabilities, as did a table grammar for SANAD and an audit witness.

## 1. BASIRA: what one calibration bank guarantees

**The observation.** In 2.1, 8 of 40 intact test rings raised an alert at a 5% target. That record attributed the gap to finite-sample variation.

**The finding.** The shipped calibration parts score lower than fresh parts from the same generator, with a mean of 0.359 against 0.475–0.497. On 4,000 independent normal parts, the shipped bank's long-run false-alert rate is about 12.9%. Its threshold sits at the 87th percentile of normal scores; under exchangeability it should sit near the 96th. A bank at least this bad occurs with probability 0.87^24 ≈ 3.7%. The bank is unlucky, not mis-specified, and the 20% measured on 40 parts is a noisy estimate of about 13%.

**Why it matters.** Split conformal guarantees a false-alert rate of at most α *on average over calibration draws*. For the one bank that is deployed, the long-run rate of the order-statistic threshold s_(k) is random. The probability that it exceeds α is P(Binomial(m, 1−α) ≥ k). For the shipped rule at m=24 that probability is 29.2%. Increasing m does not shrink it, because the marginal rule targets the mean.

**What changed.**
- Every BASIRA result now reports `training_conditional`: the exact rational exceedance probability for its rank, the PAC rank for δ (default 0.1) and the minimum calibration size. At α = 5% and δ = 10% the minimum is **45 parts**.
- `"guarantee": {"type": "pac", "delta": "0.1"}` in the payload selects the PAC rule: the smallest k whose exceedance is at most δ. When m is too small the result becomes advisory. Ties stay conservative.
- The descriptor identity `basira/registered-patch-nn/96px/float64/v2` is unchanged, so the bank digest still matches 2.1 (`4ffef5b1…`). The engine identity is now `basira/patch-nn-v2/conformal-marginal-pac/3`; `python -m mizan upgrade` moves existing stores to it.

**Repeated-calibration study** (`reports/evaluation/basira.json → calibration_study`). The study drew 400 independent banks per size from a pool of 4,000 normals. Each bank was evaluated on a separate pool of 4,000 normals and on 400 defects.

| m | Rule | Mean false alerts | Banks above 5% | Theory | Detection |
|---|---|---|---|---|---|
| 24 | Split conformal | 3.5% | 25.3% | 29.2% | 100% |
| 50 | Split conformal | 3.7% | 22.5% | 27.9% | 100% |
| 50 | PAC, δ = 10% | 1.8% | 4.3% | 7.7% | 100% |
| 100 | Split conformal | 4.4% | 30.0% | 43.6% | 100% |
| 100 | PAC, δ = 10% | 1.7% | 1.5% | 3.7% | 100% |

The marginal promise holds: every mean is at most 5%. Yet roughly a quarter of individual banks exceed it. The PAC rule keeps that share inside δ. Measured shares fall below the theoretical ones partly because each bank is drawn from a finite 4,000-part pool, which understates bank-to-bank variance. Detection stayed at 100% because these generated defects are strong; real, subtle defects can trade detection for the stricter threshold.

## 2. RASID: a confounded comparison, separated

**The observation.** 2.1 reported that the planner cost 0.26% more than the seasonal baseline, despite an 8.9% lower forecast error.

**The finding.** The candidate optimized (1−0.35)·EV + 0.35·CVaR. The baseline, with a single scenario, effectively optimized expected cost alone. The comparison therefore mixed forecast quality with the price of risk aversion. The evaluation now solves five policies on the same battery and tariff. Each policy keeps its own carried battery state and month-to-date peak.

| Policy | Total cost | Share of achievable battery value | Mean of worst 10% days |
|---|---|---|---|
| Hindsight reference | 16,252.80 | 100% | 316.30 |
| Candidate, risk-neutral | 16,383.52 | 87.1% | 322.81 |
| Seasonal baseline | 16,401.90 | 85.3% | 323.89 |
| Candidate, risk weight 0.35 | 16,441.57 | 81.4% | **321.12** |
| No battery | 17,266.07 | 0% | 340.32 |

The table shows three results:
- **Price of risk aversion.** The risk premium is 58.05 over 60 days. Its 95% moving-block bootstrap interval for the mean daily difference, [0.34, 1.65], excludes zero.
- **What the premium buys.** The risk-weighted plan has the best tail among forecast-driven policies, which is what the CVaR term is for.
- **Value of the forecast.** The risk-neutral candidate beats the seasonal baseline by 18.38 and is cheaper on 33 of 60 days. Its interval, [−0.71, 0.07], includes zero, so 60 generated days do not settle the forecast's value.

All 300 plans passed the independent verifier. The hindsight reference plans each day on the realized load under the same daily constraints. Because state carries between days it is a reference, not a global bound. The 2.1 summary columns are preserved unchanged.

## 3. SANAD grammar 3: multi-model tables

Datasheets usually compare several models in one table. Grammar 3 (`sanad/closed-ups-grammar/3`) accepts tables delimited by `|` or tabs:

- **Header.** The first cell, or the last cell for right-to-left extraction, is a model label. An optional unit column may follow it. Every other cell must be a distinct model identifier.
- **Rows.** Each data row must have exactly the header's width.
- **Claims.** Only the offered model's cell becomes a claim. The claim carries the row span, the exact cell span (and unit-cell span) and the header span that binds the column to the model.
- **Abstention.** A shape error, an empty or dash cell, an unusable or duplicated model header, or a footnote marker returns REVIEW. So do qualifiers such as "up to".
- **Context.** A table that does not name the offered model is ignored. Prose after a table needs a new model heading.
- **Unscored rows.** Rows such as "Input voltage" block like any uncovered line until a reviewer acknowledges them. Related rows such as "Efficiency (eco mode) | 99 %" cannot be acknowledged.
- **Tabs.** A tab is no longer treated as an unsafe control character, because it is a table delimiter.

**Evaluation.** Sixteen table fixtures, written with the change, are decided 16/16 with zero false MEETS and 8 decisive. A reader that takes the first number on the attribute row gets 2/16. It produces two false MEETS: one by reading a neighbouring model's column, one from a table that does not mention the offered model. The original 36-case set and the grammar-2 extension are unchanged at 36/36 and 16/16.

## 4. Audit witness (acceptance case A03)

`mizan/core/witness.py` is a separate trust domain with its own Ed25519 key, state and host lock. It mirrors entry hashes, and accepts a checkpoint only if the submitted entries extend what it has already witnessed:
- each sequence follows the last;
- each `prev` matches the last hash;
- each hash recomputes;
- each entry was written by that generation.

It refuses forks, gaps, foreign entries and checkpoints from a superseded generation. Every decision, including each refusal, is recorded.

**Store identity and generations.** A store now has a stable `store_id` that survives restores; the generation changes on each restore. `register_generation` handles a restored store:
- **Behind the witnessed head.** The restored store is accepted with a signed, explicit recovery gap that lists the lost sequence range per tenant.
- **Ahead of the witnessed head.** It must supply the connecting entries.
- **Disagreeing with a witnessed hash.** It is refused as a fork.

A parent generation can be superseded only once, which keeps two restored copies from both claiming authority.

**Commands.**
- `python -m mizan witness-init DIR` creates a witness.
- `python -m mizan anchor --witness DIR` sends unwitnessed entries and stores the receipts.
- `python -m mizan audit --witness DIR` reports, per tenant, WITNESSED, UNANCHORED_TAIL (with the age of the oldest unanchored entry), DIVERGED or REGRESSED.
- `audit --witness-key B64` verifies the stored receipts offline, which is weaker because deleted receipts go unnoticed.
- `restore --accept-history-gap` restores a backup older than the current history, on purpose: the audit log records the operator's decision and the next `anchor` records the gap.

**Tests.** The tests rewrite witnessed history the way a local administrator could, by dropping the trigger and re-hashing, which the local chain alone cannot detect. They swap in an old copy of the store, forge batches, restore behind and ahead of the witness, send late checkpoints from a displaced generation, attempt a second restore from the same parent and tamper with receipts.

**Boundary.** A witness on the same host is a protocol simulation. Independence from the broker's administrator comes from separate hosts, accounts and backups, which code cannot demonstrate. Witness-gated activation (`PENDING_WITNESS`, §5.5) is not implemented.

## 5. Evidence

| Item | Result |
|---|---|
| Tests | 80 tests: 78 executed and passed, 2 HTTP tests skipped without FastAPI (`reports/application-tests.txt`). New in 2.3: `test_basira_pac.py`, `test_sanad_tables.py`, `test_witness.py`, `test_evaluation.py`. |
| Evaluation | `reports/evaluation/{sanad,basira,rasid}.json` now carry the 2.3 analyses and their numerical environment. The 2.1 originals are preserved in `reports/evaluation/release-2.1/`. |
| Report | `reports/evaluation/index.html` is self-contained, makes no network requests and works offline. It has a hover readout on every chart, a data table for every chart, and dark mode. |
| Browser | The exhibit, the three Spaces and the report pass at 1280 px and 390 px with zero page errors and zero external requests (`reports/browser-checks.json`). |

## 6. Compatibility

- **Existing stores.** The schema migrates to version 3 on open. That adds `witness_receipts` and `store_id`, and appends one audit event. Run `python -m mizan upgrade` once: SANAD moves to grammar 3 and BASIRA to engine 3, and source approvals are kept.
- **BASIRA outputs.** Existing fields keep their meaning under the default marginal rule; `training_conditional`, `guarantee` and `descriptor` are new.
- **Bundled exhibit and Spaces.** These remain the signed 2.1 recordings, verifiable against the pinned key.
- **Not executed here.** As in 2.2, the HTTP layer and the PDF path were not exercised, because FastAPI and PyMuPDF are not installable without network access.
