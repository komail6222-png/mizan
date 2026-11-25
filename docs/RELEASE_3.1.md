# MIZAN 3.1 | Foundation models, measured on the held-out protocol

25 November 2026 · Builds on 3.0.

3.0 pinned three Hugging Face checkpoints and could import them with a verified, signed manifest, but none of its reported numbers used them. 3.1 asks one question per system: **does a pretrained foundation model do better than MIZAN's current component, on the same held-out data and the same metrics?** Each model replaces one component, frozen, inside the existing evaluation protocol, and the answer is published whichever way it goes. It is mixed: no model is better on every measure, and most differences are not resolved by this protocol. Of the two economic comparisons with the current forecaster, the risk-neutral one is not resolved and the risk-weighted one is resolved against Chronos (+1.060 per day, 95% interval +0.328 to +1.641), a result shaped by the fixed rule that turns Chronos's quantiles into planning scenarios as well as by the forecaster. The deployed engines, profiles, signed exhibit and pinned key are unchanged.

3.1 also carries the interface redesign that followed 3.0.

## 1. A redesigned interface and a bilingual introduction

- **Exhibit, Spaces and `mizan serve`.** A new instrument-like design with an embedded Readex Pro typeface (Arabic and Latin, `mizan/web/fonts/`, OFL), so no page fetches a font. New views are drawn from data the old page did not show: an image wipe and per-patch readout for BASIRA, the calibration bank against its alert threshold, linked hour-by-hour forecast and battery charts, the verifier's eleven constraint groups and the scenario costs for RASID. The pages were rebuilt from the same signed packet (`scripts/build_exhibit.py`, `scripts/build_spaces.py`); the in-page verification code is unchanged and the pinned key `9a627609…df436` still applies.
- **A bilingual introduction at the site root** (`site/landing.html`, built by `scripts/build_site.py`). A balance weighs the seven signed results while the browser recomputes each signature, and tips when one digit is changed. English and Arabic, with right-to-left layout. Every figure on it is read from `reports/` at build time, and the build refuses to run if a sentence no longer matches the evidence: "resolved" and "not resolved" must match the sign of each 95% interval, every forecast-accuracy difference is stated with its paired interval from `reports/hf/rasid_intervals.json` (which must have been derived from the evidence files as they are), each other comparison the foundation-model block states in words (roughly equal, more or less often, by datasheet language) must still hold in `reports/hf/`, and the test counts must match `reports/application-tests.txt`, including where its skipped tests ran. The exhibit moved to `/exhibit/`.
- **Checks.** `scripts/check_exhibits.py` audits WCAG AA text contrast from the computed styles of the rendered pages (the exhibit views and Spaces, both report pages in their light and dark palettes, and the introduction in English and Arabic) and fails on any violation: 0 failures in 3,608 text-element checks (`reports/browser-checks.json`). It also checks that keyboard focus stays on a control the page redraws. With `--site` it checks the introduction in Chromium (verification, tamper and restore, Arabic RTL, no overflow, zero external requests), and the Pages workflow runs it before every deploy, with Playwright pinned and Pages write access given only to the deploy job.

## 2. Three foundation models on the held-out protocol

| System | Model (pinned revision) | Replaces | Measured on |
|---|---|---|---|
| BASIRA | `facebook/dinov2-small` (`ed25f3a31f01`) | the hand-built 96 px patch descriptor | the frozen 80-item test (40 intact, 40 defects) and the repeated-calibration study, 400 banks per size m = 24, 50, 100 |
| RASID | `amazon/chronos-bolt-tiny` (`a0e552de8349`) | the ridge forecaster, for load and solar | the same 60 rolling origins, planner, verifier and settlement, each policy carrying its own battery state and month-to-date peak |
| SANAD | `intfloat/multilingual-e5-small` (`fd1525a9fd15`) | the lexical evidence retriever | a generated retrieval set: 120 datasheets (60 English, 60 Arabic), 488 (document, attribute) pairs |

**Verified import.** Every checkpoint is imported at a fixed revision with Git blob and LFS checks, a weight SHA-256 and a signed local manifest (`python -m mizan hf fetch`). The loaders in `mizan/models.py` verify the import before loading anything and refuse an unverified checkpoint. Each report records the repository, revision, weight SHA-256 and two manifest digests: `manifest_object` (the signed manifest object, reproducible by anyone re-importing the pin) and `manifest_sha256` (this machine's signed import file).

**Fidelity gate.** Before any model result is kept, the current component runs through the new harness and must reproduce its results: the shipped ones for BASIRA and RASID, and its own across two runs for SANAD, which has no shipped retrieval figures. A mismatch stops the run with exit code 2 and writes nothing.

| System | Gate | Checks | Mismatches |
|---|---|---|---|
| BASIRA | exact equality with `reports/evaluation/basira.json`: detections, false alerts, bank digest, every item's seed, condition, p-value and flag, every calibration-study field | 368 | 0 |
| RASID | the ridge side of the same run against `reports/evaluation/rasid.json`: dates, coverage and verified-plan counts exactly, load MAE within 1e-9 kW, the `candidate` and `candidate_risk_neutral` totals within 0.05% relative, other totals within 1e-6 relative | 253 | 0 |
| SANAD | two separate runs: the same retrieval-set digest and query count, the engine's own `sanad.engine.retrieve` as the lexical ranker, and all 27 lexical metrics exactly equal | 30 | 0 |

**Determinism.** `torch.manual_seed(0)`, `torch.use_deterministic_algorithms(True, warn_only=True)`, 8 torch threads, `torch.inference_mode()`. Each system was then evaluated a second time in the same process with the model loaded again; all three repeats were identical (0 differing values).

## 3. Results

Every figure below is in `reports/hf/{basira,rasid,sanad}.json`, or for the accuracy intervals and risk premiums in `reports/hf/rasid_intervals.json`, and on the report's models page (`reports/evaluation/models.html`, served as `report/models.html`).

### BASIRA: DINOv2-small as the inspection descriptor

| Measure | Shipped descriptor | DINOv2-small |
|---|---|---|
| Defects detected, frozen test | 40 / 40 | 40 / 40 |
| False alerts on intact rings, frozen test | 8 / 40 | 1 / 40 |
| Shipped bank (m = 24): long-run false-alert rate | 12.9% | 1.0% |
| How often a bank this bad or worse occurs | 3.7% | 78.6% |
| Study, m = 24: mean false alerts / banks above 5% | 3.5% / 25.3% | 3.5% / 25.0% |
| Study, m = 50: mean false alerts / banks above 5% | 3.7% / 22.5% | 3.5% / 21.3% |
| Study, m = 100: mean false alerts / banks above 5% | 4.4% / 30.0% | 4.3% / 36.0% |

The frozen test and the long-run rate rest on one bank of 24 calibration parts. That bank is an unlucky one for the shipped descriptor (a bank this bad or worse occurs 3.7% of the time) and an ordinary one for DINOv2 (78.6%). Over 400 banks per size, under the shipped split-conformal rule, the mean false-alert rates differ by at most about 0.2 percentage points (0.20 at m = 50), within the ±0.35-point precision the study states for each rate, so they are roughly equal. The share of banks above 5% is lower with DINOv2 at m = 24 and 50 and higher at m = 100; no interval is reported for these shares. Detection stayed at 100% for both at every size. DINOv2's 16 × 16 grid means the engine's top-2.5% rule averages 7 of 256 patches, against 4 for the shipped descriptor (`patches_averaged`).

**Reading.** DINOv2 cuts the frozen test's false alerts from 8 to 1, but the study shows that most of that gain comes from the one bank. Equal mean false-alert rates over banks are what split-conformal calibration predicts for any descriptor, so they are no evidence about DINOv2 itself, and detection is at 100% for both, so it does not separate them either.

### RASID: Chronos-Bolt-tiny as the load and solar forecaster

| Measure (60 days) | Ridge forecaster | Chronos-Bolt-tiny |
|---|---|---|
| Load MAE (kW) | 2.794 | 2.572 |
| Mean pinball loss, levels 0.1, 0.5 and 0.9 | 0.808 | 0.848 |
| Truncated CRPS, levels 0.1, 0.5 and 0.9 | 1.485 | 1.532 |
| 80% interval coverage of load | 74.7% | 65.1% |
| Total cost, risk-neutral planner | 16,383.52 | 16,375.43 |
| Total cost, risk-weighted planner | 16,441.88 | 16,505.49 |

Paired daily differences in forecast accuracy (Chronos minus ridge, day by day), with the same bootstrap as the cost comparisons applied to the per-day values in `reports/hf/rasid.json` (`scripts/derive_hf_intervals.py`, no model re-run):

| Measure | Mean daily difference | 95% interval | Reading |
|---|---|---|---|
| Load MAE (kW) | −0.221 | −0.698 to +0.118 | Not resolved, spans zero |
| Mean pinball loss | +0.040 | −0.063 to +0.135 | Not resolved, spans zero |
| Truncated CRPS | +0.047 | −0.138 to +0.219 | Not resolved, spans zero |
| 80% interval coverage | −9.6 points | −16.7 to −0.3 points | Lower, excludes zero |

Paired differences in mean daily cost (the Chronos policy minus the reference; negative means Chronos cost less), 95% moving-block bootstrap intervals with 7-day blocks, 4,000 draws, seed 2026:

| Comparison | Mean daily difference | 95% interval | Days cheaper / costlier | Reading |
|---|---|---|---|---|
| Chronos risk-neutral − ridge risk-neutral | −0.135 | −0.507 to +0.154 | 27 / 33 | Not resolved, spans zero |
| Chronos risk-neutral − seasonal baseline | −0.441 | −0.910 to −0.018 | 38 / 22 | Cheaper, excludes zero |
| Chronos risk-weighted − ridge risk-weighted | +1.060 | +0.328 to +1.641 | 18 / 42 | Costlier, excludes zero |

Risk-weighted plans (both forecasters with the risk weight 0.35):

| Measure (60 days) | Ridge forecaster | Chronos-Bolt-tiny |
|---|---|---|
| Total cost | 16,441.88 | 16,505.49 |
| Mean of the worst 10% of days (empirical CVaR90) | 321.07 | 320.47 |
| Worst day | 366.27 | 362.30 |
| Premium over the same forecaster's risk-neutral plan, per day (95% interval) | +0.973 (+0.338 to +1.657) | +2.168 (+1.565 to +2.755) |

**Reading.** On these 60 days the accuracy differences are not resolved, except coverage: the mean load error is 2.572 kW for Chronos's median against 2.794 for ridge, and the mean pinball loss and CRPS are 0.848 and 1.532 against 0.808 and 1.485, but the paired intervals of all three differences span zero. Its 80% intervals cover fewer load hours (65.1% against 74.7%), a resolved difference. With the same risk-neutral planner, the forecast value of Chronos over ridge is not resolved by these 60 days. Against the seasonal baseline, the Chronos risk-neutral policy is cheaper with an interval that excludes zero, though its upper end (−0.018) is close to it; the ridge forecast's own value against that baseline was not resolved in 3.0, and neither is the ridge forecast's lower load error against the seasonal baseline (−0.274 kW per day, 95% interval −0.559 to +0.056). The risk-weighted planner built on Chronos's quantiles by the fixed scenario rule costs more than the ridge one, and that interval excludes zero; the scenario rule shapes that result as well as the forecaster (below). All 420 plans (seven policies × 60 days) passed the independent verifier.

Scenarios: five equally weighted paths from the Chronos quantiles at 0.1, 0.3, 0.5, 0.7 and 0.9, load path j paired with solar path 4 − j (high load meets low solar), each path held at one quantile level for all 24 hours. The ridge policies keep their four reduced residual scenarios as shipped, built from past days' forecast errors. The risk-weighted planner minimises 0.65 × expected scenario cost + 0.35 × CVaR at β = 0.90 (`mizan/rasid/solver.py`, settings from `mizan.examples.settings`). Because every Chronos scenario weighs 0.2, at least 1 − β, the CVaR term is the cost of the costliest of the five scenarios alone, so the risk weight acts on one fixed-level path. Chronos's own risk premium (+2.168 per day) is larger than ridge's (+0.973), while its realised tail is no worse (mean of the worst 10% of days 320.47 against 321.07, worst day 362.30 against 366.27; no interval for these). The risk-weighted comparison therefore measures the scenario rule as well as the forecaster. Chronos quantiles are sorted across levels at each hour (monotone rearrangement), and 2,707 negative solar values were clipped to zero before planning (solar is not scored).

### SANAD: multilingual-E5-small as the evidence retriever

| Query, metric | Lexical (shipped) | multilingual-E5-small |
|---|---|---|
| Bilingual (the engine's), recall@1 | 96.9% | 80.5% |
| Bilingual (the engine's), recall@3 | 100.0% | 100.0% |
| Bilingual (the engine's), MRR@4 | 0.985 | 0.903 |
| English label only, recall@1 | 61.1% | 77.7% |
| English label only, recall@3 | 77.3% | 99.6% |
| English label only, MRR@4 | 0.695 | 0.879 |
| Arabic label only, recall@1 | 60.2% | 72.5% |
| Arabic label only, recall@3 | 77.3% | 93.2% |
| Arabic label only, MRR@4 | 0.688 | 0.826 |

**Reading.** With the engine's bilingual query, E5 ranks the claim line first less often than the lexical retriever (80.5% against 96.9%). That comparison is near its ceiling for the lexical retriever: the grammar accepts only lines that start with the exact attribute label, and the engine's bilingual query is exactly those labels (English and Arabic joined), so a character-trigram match finds them; 473 of 488 pairs are ranked first, and every English datasheet scores 100%. With a query in one language, E5 ranks the claim line first more often: 77.7% of the time against 61.1% with the English label alone, and 72.5% against 60.2% with the Arabic label alone. That gain is entirely cross-lingual: with the English label on Arabic datasheets E5 scores 69.9% against 23.7%, and with the Arabic label on English datasheets 67.4% against 25.1%, a query the engine never makes (it always joins both labels). On datasheets in the query's own language the lexical retriever ranks the claim first more often (100.0% against 85.8% in English, 94.0% against 77.5% in Arabic). No interval is reported for these rates: the 488 pairs share 120 datasheets, the same few query strings (each attribute's labels) and one fixed pool of near-miss lines per language, so they are not independent, and the per-pair ranks are not in the evidence, so an interval would need a new run.

`queries` in the report counts (document, attribute) pairs: 488 (239 on English datasheets, 249 on Arabic ones), each ranked with three query kinds (bilingual, English label only, Arabic label only). Both retrievers rank every line of the datasheet, the heading included; the engine returns its top four, so reciprocal rank is 0 beyond rank four. Gold is the line `sanad.engine.parse_document` extracts as the claim for each attribute, and the generator stops unless every attribute line is that claim and no other line is any claim. Verdicts are not recomputed from either ranking.

## 4. Provenance and reproduction

`reports/hf/{basira,rasid,sanad}.json` were produced by commit `c5becb0` and have not changed since. The canonical command is

```bash
python scripts/evaluate_hf.py --models var/hf --out reports/hf --repeat
```

run in the optional `.venv-hf` environment (`requirements-hf.txt`, CPU-only torch from the PyTorch CPU index; `requirements-hf.lock.txt` lists that environment's full package set) with `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`, as `scripts/evaluate_hf.py` documents. `docs/TECHNICAL_GUIDE.md` gives the setup commands, how to check a reproduction in a scratch directory, the fields that differ by design on another machine, and the measured memory and disk use. `reports/hf/rasid_intervals.json` was then derived from `rasid.json` by `scripts/derive_hf_intervals.py` (torch-free). `--out` is required, and `reports/hf` is refused without `--repeat`, so every published file records its repeat run. The environment recorded in each file: Python 3.12.16, NumPy 2.3.5, SciPy 1.17.0, torch 2.15.0+cpu, transformers 4.57.6, tokenizers 0.22.2, chronos-forecasting 2.3.2, 8 torch threads, on Linux x86_64 with an Intel Core i7-13645HX. Elapsed: BASIRA 868.8 s (repeat 850.5 s), RASID 13.2 s (11.4 s), SANAD 15.2 s (13.2 s).

CI stays torch-free: the harnesses are tested with stub models in `tests/test_hf_evaluation.py`, and the four tests that load the real checkpoints run only when torch is installed and `MIZAN_HF_MODELS` points at a verified cache.

## Disclosures

- **Ridge per-day costs differ from the shipped file.** The ridge risk-weighted policy (`candidate`) in this run differs from `reports/evaluation/rasid.json` on 22 of the 60 days, by up to 0.35 on one day (0.117% of that day's cost), while its 60-day total is within 1.9e-5 relative (16,441.88 against 16,441.57). The risk-neutral ridge policy, the seasonal baseline, no battery and perfect foresight agree within 1e-9 on every day. The gate checks totals, with a 0.05% tolerance for the two ridge policies. The cause is the numerical environment: SciPy 1.17.0 here against SciPy 1.18.1 in the release build (which also used Python 3.13.18 and NumPy 2.5.3). Its MILP solver returns different plans on some days, each passing the verifier, and the difference carries through the battery state. In a single environment the harness reproduces `evaluation.energy_evaluation` cost for cost, which a two-day test in `tests/test_hf_evaluation.py` checks. Paired comparisons use only this run's costs, never the shipped per-day costs.
- **Pinball loss and CRPS use the shared levels.** Both forecasters are scored on the levels 0.1, 0.5 and 0.9 (Chronos-Bolt is trained on 0.1 to 0.9 only), so the ridge values here (0.808 and 1.485) are not the shipped report's pinball loss and CRPS, which use the ridge forecaster's own levels. Compare Chronos only with these shared-level ridge values.
- **The SANAD bilingual query is near the lexical ceiling by construction**, as explained above. The retrieval set was fixed before any E5 number existed and was not changed afterwards.
- **One calibration bank.** BASIRA's frozen-test gain rests on one bank of 24 parts; read it with the study.
- **The risk-weighted comparison measures the scenario rule too.** With five scenarios of weight 0.2 and β = 0.90, the planner's CVaR term is the costliest fixed-level Chronos path; the ridge policies plan on residual paths. The resolved +1.060 per day is not a pure forecast comparison.
- **Many intervals, no correction.** This release reports several 95% intervals (paired cost differences, forecast-accuracy differences and risk premiums), each on its own, with no correction for multiple comparisons. The one resolved cost gain, Chronos risk-neutral against the seasonal baseline, has an upper bound of −0.018 per day.
- **Accuracy intervals are derived, not re-run.** `reports/hf/rasid_intervals.json` applies the shipped bootstrap to the per-day values already in `reports/hf/rasid.json` (and, for ridge against seasonal naive, in `reports/evaluation/rasid.json`); it names both files' SHA-256, and a test recomputes it byte for byte.
- **One settings text is out of date in the published file.** `settings.quantile_repair` in `reports/hf/rasid.json` says solar is clipped "before scoring and planning"; solar is never scored, only clipped before planning. The source text is corrected; the published file keeps the old text until the next evidence run.
- **One SANAD note predates its correction.** The first note in `reports/hf/sanad.json` calls the English-only and Arabic-only queries "the discriminating comparison"; their gain is cross-lingual and they lose within one language (above). The source notes now say to read them by datasheet language; the published file keeps the old note until the next evidence run.

## Evidence and limits

- **Tests.** 172 tests. In the main environment (Python 3.12.16), 168 passed and the 4 model-loader tests were skipped without torch; those 4 passed in `.venv-hf` against the verified checkpoints. The suite also passed on Python 3.13.18 with the same 4 skips (`reports/application-tests.txt`). New in 3.1: `tests/test_hf_models.py`, `tests/test_hf_evaluation.py`, `tests/test_hf_intervals.py`, `tests/test_site.py`.
- **The models never hold decision authority.** They supply rankings, features or forecasts. SANAD's grammar, BASIRA's calibrated threshold and RASID's independent verifier decide, as before, and nothing here changed a deployed engine. Switching an engine to a model would be a separate, signed `upgrade` decision by the operator.
- **Generated data.** All inputs are generated fixtures: rings, load and solar histories, and datasheets. No result here extends to real parts, sites or tenders.
- **CPU only.** Everything ran on one CPU; no GPU was used or qualified.
- **Frozen models.** The checkpoints were used as published, without fine-tuning.
- **One seed protocol.** Each comparison uses the shipped protocol's fixed seeds, origins and retrieval set. Other seeds were not measured; the BASIRA study shows how much one calibration bank alone can move a result.
- **Next.** Real public data (for example industrial inspection images and public load data) is the next phase, under a separate design.
