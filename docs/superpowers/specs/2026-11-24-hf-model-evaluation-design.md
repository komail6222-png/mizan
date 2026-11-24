# Design: evaluating the pinned Hugging Face models against MIZAN's engines

2026-11-24 · Status: draft for approval · Phase 2 of the "AI engineer portfolio" track (phase 3, real public data, follows separately)

## Intent

The repository pins three Hugging Face checkpoints in `mizan/models.py` and can import them with a verified, signed manifest (`python -m mizan hf fetch`), but `requirements-hf.txt` says the runtime "was not executed in this release", and `mizan.models.experiment` runs each model on a single example. The project's owner is targeting AI engineer roles. The aim is to answer, with the same rigor as the existing evaluation, one question per system: **does a pretrained foundation model from Hugging Face do better than MIZAN's current component, on the same held-out data and the same metrics?** Whatever the answer, it is published.

## Goals

1. Run each pinned model inside the existing evaluation protocol, not on a single example:
   - **BASIRA:** `facebook/dinov2-small` patch features in place of the hand-built 96 px descriptor, on the frozen 80-item test (40 intact, 40 defects) and the repeated-calibration study (400 banks per size, the same pools and seeds).
   - **RASID:** `amazon/chronos-bolt-tiny` forecasts for load and solar on the same 60 rolling origins, scored for accuracy and passed through the same planner, verifier and settlement, with paired moving-block bootstrap intervals against the ridge forecaster and the seasonal baseline.
   - **SANAD:** `intfloat/multilingual-e5-small` as an evidence retriever over every document line in the 68 grammar fixtures, compared with the current lexical retriever. The grammar keeps verdict authority; the retriever only ranks candidate lines.
2. Import every checkpoint through the existing pinned path (fixed revision, Git blob and LFS checks, weight SHA-256, signed local manifest) and record those identities in each report.
3. Write machine-readable results to `reports/hf/{sanad,basira,rasid}.json` with the numerical environment (Python, NumPy, SciPy, torch, transformers, chronos-forecasting versions, thread count), model repo, revision and weight hash.
4. Show the results in the evaluation report, the README "What is verified" table, the introduction page (figures read from `reports/hf/` by `build_site.py`) and a new release note.
5. Keep CI offline and torch-free: the evaluation harness is tested with stub models.

## Non-goals

- Changing the deployed engines, profiles, signed exhibit or pinned key. Even if a model wins, switching an engine is a separate, signed `upgrade` decision for later.
- Fine-tuning. Models are used frozen, as published.
- GPU qualification. Everything runs on the CPU of the owner's machine.
- Real-world data (phase 3), the live Hugging Face Space and the further landing-page work (later projects).

## Anti-goals (failure even if it "works")

- Any comparison that is not on the same items, seeds, origins and metrics as the baseline it is compared with.
- Tuning a model's settings on the test items. Choices that need tuning are fixed before the run and written in the report.
- Reporting a win without an interval when the existing evaluation reports one (RASID), or hiding a loss.
- Leakage: a forecast context or calibration bank that sees data the original protocol excludes.
- Adding torch or transformers to `requirements.txt` or the CI install.

## Constraints

- Linux (WSL2) CPU: Intel i7-13645HX, 7 GiB RAM visible to WSL, no GPU. All three checkpoints are small (about 22M, 118M and 9M parameters).
- The optional runtime lives in a separate virtual environment (`.venv-hf`, git-ignored) installed from `requirements-hf.txt` with CPU-only torch wheels; the main `.venv` and its pins are untouched.
- `mizan/evaluation.py` and the shipped reports under `reports/evaluation/` must not change (they are evidence). New code may import their helpers.
- Determinism: fixed seeds, `torch.use_deterministic_algorithms(True)` where supported, a fixed thread count, `torch.inference_mode()`. The run is repeated once to confirm identical results, and any non-determinism is reported rather than hidden.
- Every number on public pages comes from `reports/` (CLAUDE.md invariant).

## Approaches considered

| Approach | What it is | Trade-off | Decision |
|---|---|---|---|
| 1. Run `mizan hf compare` as it is | One example per model | Fast; proves nothing and reads as such to an engineer | Rejected |
| **2. Full evaluation on the existing held-out protocol** | Each model replaces the matching component inside the same harness and metrics | More work and about 1 GB of optional dependencies; produces evidence comparable to the shipped numbers | **Chosen by the owner** |
| 3. Approach 2 plus real public datasets | MVTec AD for BASIRA, public load data for RASID | Much stronger credibility; dataset licences (MVTec AD is non-commercial), downloads and longer runs | Next phase, separate design |

## Design

### Module layout

- `mizan/hf_evaluation.py` (new): the three evaluations, written against small interfaces so tests can inject stubs:
  - `basira_hf(feature_fn, profile)` reuses `basira.engine.fit/score/rank_decision/pac_rank/exceedance` and `examples.part`, mirroring `vision_evaluation()` and `calibration_study()` item for item (same seeds 9100–9179, pools 30000+, 40000+, 50000+, same draws and sizes).
  - `rasid_hf(forecaster)` mirrors `energy_evaluation()`: same 60 origins, the same vintage rule (context only from records known at the origin), the same per-policy carried state and month-to-date peak, `solve`, `verify`, `realized`, `decision_summary` and `paired_comparison`.
  - `sanad_hf(embed_fn)` builds a retrieval task from the grammar fixtures (below).
- `mizan/models.py`: gains `feature_fn(kind, root)` / `forecaster(root)` / `embedder(root)` loaders that wrap the verified local checkpoint (reusing `verify_local`), so the evaluation never loads an unverified file. `experiment()` stays for the CLI.
- `scripts/evaluate_hf.py` (new): `--models var/hf --out reports/hf [--only basira|rasid|sanad]`; writes the three JSON files with `canonical()`.
- `tests/test_hf_evaluation.py` (new): runs each evaluation with deterministic stub models on reduced sizes to check wiring, shapes, metric definitions and the no-leakage rule; no torch import.

### Harness fidelity gate

Before any model runs, each new evaluation re-runs the **current** component through the new harness and must reproduce the shipped results in `reports/evaluation/`: BASIRA's 40/40 detected, 8/40 false alerts, bank digest and every calibration-study row exactly; SANAD's lexical retriever on the same task deterministically; RASID's ridge `candidate` and `candidate_risk_neutral` costs within the documented solver tolerance (`reports/reproduction-2.2.json`, `reports/local-verification.md`), with identical forecast errors. A mismatch stops the run. This is what makes "same protocol" checkable rather than asserted, and the gate's result is written into each report.

### BASIRA with DINOv2

- Features: the existing preprocessing in `models.experiment` (bicubic resize to 224, ImageNet normalisation, last hidden state without the CLS token, L2-normalised, reshaped to a 16 × 16 grid). `basira.engine.score` accepts any (H, W, D) grid; its `tile` argument is a memory batch over bank items, not a spatial tile, so no adjustment is needed. The top-2.5% patch rule then averages 7 of 256 patches (4 of 144 for the shipped descriptor); the report states this.
- Reported side by side with the shipped descriptor: detected / 40, false alerts / 40, the shipped-size bank's long-run false-alert estimate, and the calibration-study rows (mean false alert, share of banks above α, PAC share, detection) for m = 24, 50, 100.
- Runtime: about 8,500 forward passes; batch them.

### RASID with Chronos-Bolt

- Context: load and solar histories known at each origin (the same `vintage` rule), at most the model's context length; univariate forecasts for each series, 24 steps.
- Accuracy: MAE of the median for load (comparable to `ridge_mae_kw` and `seasonal_naive_mae_kw`), mean pinball loss and truncated CRPS with the existing `quantile_scores` on the levels both forecasters produce, 0.1, 0.5 and 0.9 (Chronos-Bolt is trained on 0.1 to 0.9 only), and 80% interval coverage (0.1 to 0.9).
- Decisions: two new policies on the same settings, `chronos` (risk weight 0.35) and `chronos_risk_neutral`, with five equally weighted scenarios from the Chronos quantile paths at 0.1, 0.3, 0.5, 0.7 and 0.9. Load path j is paired with solar path 4 − j (high load with low solar), the conservative choice. The ridge policies keep their four reduced residual scenarios exactly as shipped. Each policy carries its own battery state and peak. Paired comparisons: `chronos_risk_neutral` against `candidate_risk_neutral` (forecast value of Chronos over ridge, same planner) and against `seasonal_naive`; `chronos` against `candidate`.
- Every plan goes through the independent verifier; the count of verified plans is reported.

### SANAD with multilingual E5

- Revised during planning: the 68 grammar fixtures hold one or two lines per document, so ranking them is trivial and both retrievers would score 100%. The retrieval task instead uses a **generated retrieval set** written for this evaluation: 120 seeded datasheets (half English, half Arabic), each with a model line, three to five scored attribute lines and four to six near-miss distractor lines (for example eco-mode efficiency, active versus apparent power, derating notes, input voltage). Nothing is tuned on it.
- Gold: the lines `sanad.engine.parse_document` extracts as claims for each attribute. The generator asserts that every attribute line is extracted and no distractor is, so the grammar, not the retrievers, defines the answer.
- Retrievers: the engine's own `sanad.engine.retrieve` (character-trigram TF-IDF, unchanged) and E5 (`query:` / `passage:` prefixes, mean pooling, cosine), ranking every line of the document.
- Queries: the engine's bilingual query (English and Arabic labels joined, as `assess` does) is the primary comparison; English-only and Arabic-only queries measure cross-lingual retrieval.
- Metrics: recall@1, recall@3 and mean reciprocal rank (the engine returns its top four, so reciprocal rank is 0 beyond four), overall and by document language.
- Verdicts are not recomputed from E5 rankings; the report says so.

### Reporting and presentation

- `reports/hf/*.json`: results, protocol notes, fixed settings, model identity (repo, revision, weight SHA-256, manifest digest), environment, elapsed time, and the repeat-run check.
- `scripts/build_report.py`: a new "Foundation models from Hugging Face" section with one compact table or chart per system, read from `reports/hf/`, within the page's size budget (if the budget no longer fits, split the section into its own page rather than relax the test).
- `scripts/build_site.py` and `site/landing.html`: one evidence entry per system, figures read from `reports/hf/`, with the same "resolved / not resolved" interval guard for RASID.
- `docs/RELEASE_3.1.md`, `CHANGELOG.md`, `README.md`, `docs/QUALIFICATION.md` (model-profile rows), `reports/application-tests.txt` and `reports/local-verification.md` updated.

## Calls made while writing (check these)

- Solar forecasts below zero are clipped to zero before planning; the clipping count is reported.
- If `torch.use_deterministic_algorithms(True)` rejects an operation on CPU, it runs with `warn_only=True` and the repeat-run check decides whether results are stable; the report states which.
- The optional runtime is installed in `.venv-hf` with CPU-only torch wheels (about 1 GB on disk); nothing in the main environment changes.
- The deployed engines stay as they are whatever the outcome; this phase only measures.

## Left to the builder

- Exact batch sizes and thread count (fixed and documented).
- Pinned versions for torch, transformers and chronos-forecasting within `requirements-hf.txt`'s ranges, recorded in the reports.
- Table versus chart choices in the report, within the existing visual identity.
