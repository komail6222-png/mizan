# MIZAN 3.1 | Foundation models, measured on the held-out protocol

Unreleased (prepared 25 November 2026) · Builds on 3.0. The version strings in `pyproject.toml` and `CITATION.cff`, the tag and the GitHub release change together when the owner approves publication.

3.0 pinned three Hugging Face checkpoints and could import them with a verified, signed manifest, but none of its reported numbers used them. 3.1 asks one question per system: **does a pretrained foundation model do better than MIZAN's current component, on the same held-out data and the same metrics?** Each model replaces one component, frozen, inside the existing evaluation protocol, and the answer is published whichever way it goes. It is mixed: no model is better on every measure. Of the two economic comparisons with the current forecaster, the risk-neutral one is not resolved and the risk-weighted one is resolved against Chronos (+1.060 per day, 95% interval +0.328 to +1.641). The deployed engines, profiles, signed exhibit and pinned key are unchanged.

3.1 also carries the interface redesign that followed 3.0.

## 1. A redesigned interface and a bilingual introduction

- **Exhibit, Spaces and `mizan serve`.** A new instrument-like design with an embedded Readex Pro typeface (Arabic and Latin, `mizan/web/fonts/`, OFL), so no page fetches a font. New views are drawn from data the old page did not show: an image wipe and per-patch readout for BASIRA, the calibration bank against its alert threshold, linked hour-by-hour forecast and battery charts, the verifier's eleven constraint groups and the scenario costs for RASID. The pages were rebuilt from the same signed packet (`scripts/build_exhibit.py`, `scripts/build_spaces.py`); the in-page verification code is unchanged and the pinned key `9a627609…df436` still applies.
- **A bilingual introduction at the site root** (`site/landing.html`, built by `scripts/build_site.py`). A balance weighs the seven signed results while the browser recomputes each signature, and tips when one digit is changed. English and Arabic, with right-to-left layout. Every figure on it is read from `reports/` at build time, and the build refuses to run if a sentence no longer matches the evidence: "resolved" and "not resolved" must match the sign of each 95% interval, each comparison the foundation-model block states in words (lower, worse, roughly equal, more or less often) must still hold in `reports/hf/`, and the test counts must match `reports/application-tests.txt`, including where its skipped tests ran. The exhibit moved to `/exhibit/`.
- **Checks.** Text colours meet WCAG AA contrast on every page, checked against the rendered pages. `scripts/check_exhibits.py --site` checks the introduction in Chromium (verification, tamper and restore, Arabic RTL, no overflow, zero external requests), and the Pages workflow runs it before every deploy.

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

Every figure below is in `reports/hf/{basira,rasid,sanad}.json` and in the "Foundation models from Hugging Face" section of the evaluation report (`reports/evaluation/index.html#hf-section`).

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

**Reading.** DINOv2 cuts the frozen test's false alerts from 8 to 1, but the study shows that most of that gain comes from the one bank: on average over banks, the two descriptors are about equally calibrated on these generated rings.

### RASID: Chronos-Bolt-tiny as the load and solar forecaster

| Measure (60 days) | Ridge forecaster | Chronos-Bolt-tiny |
|---|---|---|
| Load MAE (kW) | 2.794 | 2.572 |
| Mean pinball loss, levels 0.1, 0.5 and 0.9 | 0.808 | 0.848 |
| Truncated CRPS, levels 0.1, 0.5 and 0.9 | 1.485 | 1.532 |
| 80% interval coverage of load | 74.7% | 65.1% |
| Total cost, risk-neutral planner | 16,383.52 | 16,375.43 |
| Total cost, risk-weighted planner | 16,441.88 | 16,505.49 |

Paired differences in mean daily cost (the Chronos policy minus the reference; negative means Chronos cost less), 95% moving-block bootstrap intervals with 7-day blocks, 4,000 draws, seed 2026:

| Comparison | Mean daily difference | 95% interval | Days cheaper / costlier | Reading |
|---|---|---|---|---|
| Chronos risk-neutral − ridge risk-neutral | −0.135 | −0.507 to +0.154 | 27 / 33 | Not resolved, spans zero |
| Chronos risk-neutral − seasonal baseline | −0.441 | −0.910 to −0.018 | 38 / 22 | Cheaper, excludes zero |
| Chronos risk-weighted − ridge risk-weighted | +1.060 | +0.328 to +1.641 | 18 / 42 | Costlier, excludes zero |

**Reading.** Chronos's median forecast is more accurate than the ridge forecast, but its quantiles score worse: mean pinball loss and CRPS over the three levels are higher, and its 80% intervals cover 65.1% of the load hours against 74.7%. With the same risk-neutral planner, the forecast value of Chronos over ridge is not resolved by these 60 days. Against the seasonal baseline, the Chronos risk-neutral policy is cheaper with an interval that excludes zero, though its upper end (−0.018) is close to it; the ridge forecast's own value against that baseline was not resolved in 3.0. The risk-weighted planner built on Chronos's five quantile scenarios costs more than the ridge one, and that interval excludes zero. All 420 plans (seven policies × 60 days) passed the independent verifier.

Scenarios: five equally weighted paths from the Chronos quantiles at 0.1, 0.3, 0.5, 0.7 and 0.9, load path j paired with solar path 4 − j (high load meets low solar). The ridge policies keep their four reduced residual scenarios as shipped. Chronos quantiles are sorted across levels at each hour (monotone rearrangement), and 2,707 negative solar values were clipped to zero before planning (solar is not scored).

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

**Reading.** With the engine's bilingual query, E5 ranks the claim line first less often than the lexical retriever (80.5% against 96.9%). That comparison is near its ceiling for the lexical retriever: the grammar accepts only lines that start with the exact attribute label, and the engine's bilingual query is exactly those labels (English and Arabic joined), so a character-trigram match finds them; 473 of 488 pairs are ranked first, and every English datasheet scores 100%. The discriminating comparison is a query in one language: with the English label alone E5 ranks the claim line first 77.7% of the time against 61.1%, and with the Arabic label alone 72.5% against 60.2%. The gain is cross-lingual: with the English label on Arabic datasheets E5 scores 69.9% against 23.7%, and with the Arabic label on English datasheets 67.4% against 25.1%. On datasheets in the query's own language the lexical retriever still ranks the claim first more often (100.0% against 85.8% in English, 94.0% against 77.5% in Arabic).

`queries` in the report counts (document, attribute) pairs: 488 (239 on English datasheets, 249 on Arabic ones), each ranked with three query kinds (bilingual, English label only, Arabic label only). Both retrievers rank every line of the datasheet, the heading included; the engine returns its top four, so reciprocal rank is 0 beyond rank four. Gold is the line `sanad.engine.parse_document` extracts as the claim for each attribute, and the generator stops unless every attribute line is that claim and no other line is any claim. Verdicts are not recomputed from either ranking.

## 4. Provenance and reproduction

`reports/hf/` was produced by commit `c5becb0` and has not changed since. The canonical command is

```bash
python scripts/evaluate_hf.py --models var/hf --out reports/hf --repeat
```

run in the optional `.venv-hf` environment (`requirements-hf.txt`, CPU-only torch) with `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`, as `scripts/evaluate_hf.py` documents. `--out` is required, and `reports/hf` is refused without `--repeat`, so every published file records its repeat run. The environment recorded in each file: Python 3.12.16, NumPy 2.3.5, SciPy 1.17.0, torch 2.15.0+cpu, transformers 4.57.6, tokenizers 0.22.2, chronos-forecasting 2.3.2, 8 torch threads, on Linux x86_64 with an Intel Core i7-13645HX. Elapsed: BASIRA 868.8 s (repeat 850.5 s), RASID 13.2 s (11.4 s), SANAD 15.2 s (13.2 s).

CI stays torch-free: the harnesses are tested with stub models in `tests/test_hf_evaluation.py`, and the four tests that load the real checkpoints run only when torch is installed and `MIZAN_HF_MODELS` points at a verified cache.

## Disclosures

- **Ridge per-day costs differ from the shipped file.** The ridge risk-weighted policy (`candidate`) in this run differs from `reports/evaluation/rasid.json` on 22 of the 60 days, by up to 0.35 on one day (0.117% of that day's cost), while its 60-day total is within 1.9e-5 relative (16,441.88 against 16,441.57). The risk-neutral ridge policy, the seasonal baseline, no battery and perfect foresight agree within 1e-9 on every day. The gate checks totals, with a 0.05% tolerance for the two ridge policies. The cause is the numerical environment: SciPy 1.17.0 here against SciPy 1.18.1 in the release build (which also used Python 3.13.18 and NumPy 2.5.3). Its MILP solver returns different plans on some days, each passing the verifier, and the difference carries through the battery state. In a single environment the harness reproduces `evaluation.energy_evaluation` cost for cost, which a two-day test in `tests/test_hf_evaluation.py` checks. Paired comparisons use only this run's costs, never the shipped per-day costs.
- **Pinball loss and CRPS use the shared levels.** Both forecasters are scored on the levels 0.1, 0.5 and 0.9 (Chronos-Bolt is trained on 0.1 to 0.9 only), so the ridge values here (0.808 and 1.485) are not the shipped report's pinball loss and CRPS, which use the ridge forecaster's own levels. Compare Chronos only with these shared-level ridge values.
- **The SANAD bilingual query is near the lexical ceiling by construction**, as explained above. The retrieval set was fixed before any E5 number existed and was not changed afterwards.
- **One calibration bank.** BASIRA's frozen-test gain rests on one bank of 24 parts; read it with the study.

## Evidence and limits

- **Tests.** 161 tests. In the main environment (Python 3.12.16), 157 passed and the 4 model-loader tests were skipped without torch; those 4 passed in `.venv-hf` against the verified checkpoints. The suite also passed on Python 3.13.18 with the same 4 skips (`reports/application-tests.txt`). New in 3.1: `tests/test_hf_models.py`, `tests/test_hf_evaluation.py`, `tests/test_site.py`.
- **The models never hold decision authority.** They supply rankings, features or forecasts. SANAD's grammar, BASIRA's calibrated threshold and RASID's independent verifier decide, as before, and nothing here changed a deployed engine. Switching an engine to a model would be a separate, signed `upgrade` decision by the operator.
- **Generated data.** All inputs are generated fixtures: rings, load and solar histories, and datasheets. No result here extends to real parts, sites or tenders.
- **CPU only.** Everything ran on one CPU; no GPU was used or qualified.
- **Frozen models.** The checkpoints were used as published, without fine-tuning.
- **One seed protocol.** Each comparison uses the shipped protocol's fixed seeds, origins and retrieval set. Other seeds were not measured; the BASIRA study shows how much one calibration bank alone can move a result.
- **Next.** Real public data (for example industrial inspection images and public load data) is the next phase, under a separate design.
