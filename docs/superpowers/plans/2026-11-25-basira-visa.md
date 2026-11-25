# BASIRA on Real Images (VisA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate the unchanged BASIRA engine on the 12 VisA classes as customer product lines, under a protocol sealed before any test image is scored, and publish the evidence, two results pages, a landing line and docs.

**Architecture:**
- `mizan/visa.py` owns the dataset: split parsing, manifest and hash verification, engine-path preparation, and the sealed protocol and splits.
- `mizan/visa_evaluation.py` owns the harness and the statistics, and stays torch-free (scorers are injected).
- `scripts/fetch_visa.py` is the only code that touches the network. `scripts/evaluate_visa.py` runs the gates, a dry run or the real run, and writes `reports/visa/`.
- `scripts/build_visa_report.py` renders two pages from that evidence. `build_site.py` serves them and adds guarded landing facts.

**Tech Stack:** Python 3.12, NumPy, SciPy and Pillow (existing). Optional `.venv-hf` (torch 2.15.0+cpu, transformers) for the DINOv2 scorers. unittest, Playwright Chromium, WSL Ubuntu.

**Spec:** `docs/superpowers/specs/2026-11-25-basira-visa-design.md`

## Global Constraints

### Environment, frozen files and CI

- Work in WSL at `~/projects/mizan` on branch `visa-evaluation`. Use `.venv` for everything torch-free and `.venv-hf` only for the DINOv2 scorers.
- Do not modify `mizan/basira/engine.py`, `mizan/evaluation.py`, `mizan/hf_evaluation.py`, `mizan/models.py`, `reports/evaluation/`, `reports/demo/`, `reports/hf/`, or the signed exhibit pages. New code imports their functions.
- CI stays torch-free and network-free:
  - DINOv2 tests are skipped unless torch and `MIZAN_HF_MODELS` are both present.
  - No test downloads anything.
  - CI tests use the stand-in fixture from Task 1.

### Data source (verbatim)

- **Archive:** `https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar`, 1,929,840,640 bytes.
- **Split:** `split_csv/1cls.csv` from `github.com/amazon-science/spot-diff`, pinned to a commit SHA, 660,329 bytes. Columns are `object,split,label,image,mask`.
- **Licence:** CC BY 4.0. The owner approved the download.

### Classes and types

| Type | Classes |
|---|---|
| complex structure | `pcb1`, `pcb2`, `pcb3`, `pcb4` |
| multiple instances | `capsules`, `candle`, `macaroni1`, `macaroni2` |
| single instance | `cashew`, `chewinggum`, `fryum`, `pipe_fryum` |

### Protocol (sealed in Task 2, verbatim from the spec)

**Splits:**
- `VISA_SEED = 20220922`. Each class's seed is the first 8 bytes, big-endian, of `sha256(f"{VISA_SEED}:{cls}")`.
- Train normals are sorted by path, then permuted with `numpy.random.default_rng(seed).permutation`.
- The first 30 are the reference set, the next 100 the calibration set. The rest are halved: the first half is the draw pool, the second half the evaluation pool.

**Configurations:**
- **As shipped:** reference [:10], calibration [:24], marginal rule.
- **Full:** reference [:30], calibration [:100], marginal rule and PAC rule.
- In both, α = 1/20; PAC uses δ = 1/10.

**Scorers:**
- `descriptor`: the engine path.
- `dinov2-engine`: the engine path, using `models.dinov2_features`.
- `dinov2-native`: the original colour image, using `models.dinov2_features`; off the engine path.

**Statistics:**
- Clopper–Pearson 95%.
- Exchangeability by an exact Beta-Binomial convolution. A rank-k-of-m threshold's rate follows Beta(m − k + 1, k).
- AUROC as Mann–Whitney, counting ties as ½, with a stratified bootstrap of 2,000 draws, seed 2026.
- Paired scorer differences by a bootstrap stratified by class and label, 2,000 draws, seed 2026.

**Study:** the Full bank, 400 draws, m in (24, 50, 100) capped by the draw pool, seed 23. Long-run false-alert rate is measured on the evaluation pool; detection on the test anomalies.

**Example strip:** taken from the `descriptor` scorer under the Full marginal rule, pooled, with items sorted by (class, path). For each of {caught defect, missed defect, false alert, correct pass}, take the item whose score is the lower median of that set. Each is a 96×96 greyscale JPEG at quality 85. An empty set is shown as "none".

### Gate tolerances

- **Fixture half:** must equal `reports/evaluation/basira.json` exactly (bank digest, calibration m, and each row's seed, condition, p-value and flag).
- **DINOv2 half (vs `reports/hf/basira.json` model frozen test):**
  - calibration m, ranks, p-values, flags and counts must match exactly;
  - scores must agree within 1e-6 relative;
  - the bank digest is exempt.
  - Record `bit_exact: true/false`.

### Evidence and run log

- `reports/visa/basira.json` holds the evidence. `reports/visa/runs.json` holds an append-only run log of every real run: commit, UTC date, `dry_run`, reason, and protocol digest.
- `reports/visa` is refused without `--repeat`. A failed gate exits 2 and writes nothing.

### Pages, docs and commits

- Report pages: English only, Latin only, each under 60,000 characters, zero external requests, built from the evidence at build time, with no typed numbers.
- Attribution text, verbatim on the pages and in `docs/DATA_LICENSES.md`:
  > VisA (Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and Segmentation", arXiv:2207.14315), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Images were converted to 96×96 greyscale or resized for the model; no image is redistributed except the four 96 px examples shown.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Regenerate `SHA256SUMS` in the last task. The version stays 3.1.0, and the CHANGELOG entry is titled 3.2.0 (unreleased).

## Review Focus

- **Duplicates.** A train normal whose decoded 96 px image duplicates an earlier one in its class would make the engine refuse the split (`SPLIT_LEAK`). `protocol_split` drops later duplicates (by decoded-image digest, in sorted-path order) before shuffling, and records the count. A test item identical to a bank or calibration image is recorded as refused (`QUERY_LEAK`), not scored, and excluded from rates with its count disclosed. Tests in Tasks 2 and 4.
- **Changed or missing images.** `verify_manifest` refuses before any scoring (`VISA_HASH`), naming the first bad path. Test in Task 1.
- **Interrupted downloads.** A partial or failed download never becomes an archive. The fetch writes `.part`, checks the exact byte count and the SHA-256 (trust on first use, then compared with the manifest), and only then renames and extracts. Test in Task 1 with a local file URL.
- **Dry runs.** `--dry-run` must never open a test image. The test asserts no path with `split == "test"` is read (Task 6).
- **Memory on 7 GiB.** Each image's features are scored against both banks at once and dropped. Only scores are kept, never feature grids. This is checked by a feature-function spy in Task 4 and by the measured peak RSS in Task 6.

---

### Task 1: Dataset fetch, manifest and engine-path preparation

**Files:**
- Create: `mizan/visa.py`, `scripts/fetch_visa.py`, `tests/visa_fixture.py`, `tests/test_visa.py`, `reports/visa/manifest.json` (real)

**Interfaces:**
- Produces, in `mizan/visa.py`:
  - `CLASSES: tuple[str, ...]` (12 names, as in Global Constraints) and `TYPES: dict[str, str]`.
  - `ARCHIVE_URL`, `ARCHIVE_BYTES = 1929840640`, `SPLIT_URL` (pinned commit) and `SPLIT_BYTES = 660329`.
  - `read_split(path) -> list[dict]`, with keys `object, split, label, image`. It refuses (`Refused("VISA_SPLIT", ...)`) an unknown class, a label other than normal or anomaly, or a train anomaly.
  - `build_manifest(root: Path, rows, archive_sha256: str, split_sha256: str) -> dict`, with keys `archive_sha256, split_sha256, split_commit, images: [{path, sha256, object, split, label}]`, sorted by path.
  - `verify_manifest(root: Path, manifest: dict) -> None`. It raises `Refused("VISA_HASH", path)` on the first missing or changed file.
  - `engine_png(path: Path) -> str`: greyscale, then bilinear 96×96, then `basira.engine.encode_image`.
- Produces, in `tests/visa_fixture.py`: `make_visa_tree(root: Path, per_class: int = 6) -> Path`. It builds a tiny VisA-like tree: two classes from `CLASSES`, small RGB JPEGs including one anomaly per class in the test split, and a matching `1cls.csv`. It returns the CSV path.
- Produces `scripts/fetch_visa.py`:
  - CLI: `--out var/visa [--archive-url URL] [--split-url URL]`.
  - Writes `var/visa/data/...` and `reports/visa/manifest.json`. If the manifest already exists, it verifies against it instead of overwriting.

- [ ] **Step 1: Write the failing tests** in `tests/test_visa.py`:
  - `test_read_split_parses_and_refuses_train_anomalies`: on the stand-in, the parse count equals the CSV rows. A CSV with an anomaly row marked `train` raises `Refused` with code `VISA_SPLIT`.
  - `test_manifest_round_trip_and_tamper`: `build_manifest` then `verify_manifest` passes. After overwriting one image's bytes, `verify_manifest` raises `VISA_HASH` naming that path.
  - `test_engine_png_equals_engine_decode`: for a stand-in JPEG, `b.decode_image(engine_png(p)).tobytes() == Image.open(p).convert("L").resize((96, 96), Image.Resampling.BILINEAR).tobytes()`.
  - `test_fetch_refuses_wrong_size_and_keeps_no_partial`: `fetch_visa.main` with a `file://` archive one byte short exits non-zero, leaves no `.part` and no extracted tree, and writes no manifest.
  - `test_classes_match_types`: `set(TYPES) == set(CLASSES)`, and `TYPES` has exactly the three type names.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_visa -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `mizan/visa.py` and `scripts/fetch_visa.py` to the interfaces.
  - Download with `urllib.request` into `<out>/<name>.part`, streaming SHA-256, and compare the byte count with the constant. Rename, then extract with `tarfile`, refusing members with absolute paths or `..`.
  - Resolve the data root so that `root / row["image"]` exists for every row; otherwise refuse.
  - Pin `SPLIT_URL` to the `raw.githubusercontent.com/amazon-science/spot-diff/<sha>/split_csv/1cls.csv` of the current `main`. Get the SHA with `gh api repos/amazon-science/spot-diff/commits/main --jq .sha` and record it in `split_commit`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Then run the full suite: `.venv/bin/python -m unittest discover -s tests`. Expected: OK.
- [ ] **Step 5:** Run the real fetch: `.venv/bin/python scripts/fetch_visa.py --out var/visa`. Expected:
  - `reports/visa/manifest.json` with 10,821 images;
  - `set(object) == set(CLASSES)`;
  - no train anomalies;
  - the archive SHA-256 printed.
  Record the per-class train and test counts in the task report.
- [ ] **Step 6:** Commit `mizan/visa.py scripts/fetch_visa.py tests/visa_fixture.py tests/test_visa.py reports/visa/manifest.json` with the message "Fetch VisA with a verified manifest".

### Task 2: Sealed protocol and splits

**Files:**
- Modify: `mizan/visa.py`
- Test: `tests/test_visa.py`

**Interfaces:**
- Produces, in `mizan/visa.py`:
  - `PROTOCOL: dict`: every value under "Protocol (sealed in Task 2)" in Global Constraints, as plain JSON types.
  - `SEALED_DIGEST: str`, equal to `mizan.core.contract.digest(PROTOCOL)`.
  - `class_seed(cls: str) -> int`.
  - `protocol_split(root: Path, manifest: dict, cls: str) -> dict`, with keys `reference` (30 paths), `calibration` (100), `draw_pool`, `eval_pool`, `test_normal`, `test_anomaly` and `duplicates_dropped: int`.

- [ ] **Step 1: Write the failing tests:**
  - `test_protocol_digest_is_sealed`: `digest(PROTOCOL) == SEALED_DIGEST`.
  - `test_split_is_deterministic_and_disjoint`: on the stand-in, with sizes patched small via a `sizes=` keyword used only by tests, two calls are equal and the six lists are pairwise disjoint. Only train normals appear in the first four lists.
  - `test_duplicate_train_normals_are_dropped_and_counted`: copy one normal's bytes to a new path in the stand-in. `duplicates_dropped == 1`, and the later path (in sorted order) is absent.
  - `test_class_seed_known_value`: `class_seed("candle")` equals the integer the formula gives, computed in the test from `hashlib`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Duplicates are detected by `digest(b.decode_image(engine_png(p)).tobytes())`.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit `mizan/visa.py tests/test_visa.py` with the message "Seal the VisA protocol". Report this commit's SHA: it is the seal commit. No test image may be scored before it exists.

### Task 3: Statistics

**Files:**
- Create: `mizan/visa_evaluation.py`
- Test: `tests/test_visa_evaluation.py`

**Interfaces:**
- Produces, in `mizan/visa_evaluation.py`:
  - `clopper_pearson(x: int, n: int, level: float = .95) -> tuple[float, float]`. Returns (0, upper) when x = 0, and (lower, 1) when x = n.
  - `rule_rank(m: int, rule: str) -> int | None`. `"marginal"` gives `ceil((m+1)(1−α))`; `"pac"` gives `b.pac_rank(m, α, δ)`.
  - `exchangeability(classes: list[tuple[int, int]], m: int, k: int) -> dict`. Its input is (n_test_normals, false_alerts) per class. It returns `observed, expected_mean, range95: [lo, hi], p_two_sided, verdict`, where verdict is one of `"consistent"`, `"more"` or `"fewer"`.
  - `auroc(normal: Sequence[float], anomaly: Sequence[float]) -> float`.
  - `auroc_interval(normal, anomaly, draws=2000, seed=2026) -> tuple[float, float]`.
  - `paired_difference(strata: list[dict], stat: Callable, draws=2000, seed=2026) -> dict`, returning `difference, ci95, reading`. Each stratum holds the two scorers' per-item values; `reading` is decided by the interval's sign.

- [ ] **Step 1: Write the failing tests:**
  - `clopper_pearson(0, 10)[1]` is approximately `1 − 0.025 ** (1/10)`, within 1e-12.
  - `clopper_pearson(5, 5)[0]` is approximately `0.025 ** (1/5)`.
  - `rule_rank(24, "marginal") == 24`, `rule_rank(100, "marginal") == 96`, and `rule_rank(100, "pac") == b.pac_rank(100, Fraction(1,20), Fraction(1,10))`.
  - `exchangeability([(n, x)], m, k)` with one class equals a direct `scipy.stats.betabinom(n, m−k+1, k)` computation: its range95 quantiles and `expected_mean = n·(m−k+1)/(m+1)`. For two classes, the convolution's pmf sums to 1 within 1e-12.
  - `auroc([0, 1], [1, 2]) == 0.875` (one tie counted as ½). `auroc` of perfectly separated scores is 1.0.
  - `paired_difference` on identical scorers gives difference 0 and `reading == "not resolved"`. A constant shift of +1 in one scorer's detections gives `reading == "higher"`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement with `scipy.stats.beta.ppf` and `scipy.stats.betabinom`. The convolution uses `numpy.convolve` over per-class pmfs. Range95 takes the smallest x with cdf ≥ 0.025 and the smallest with cdf ≥ 0.975. `p_two_sided = min(1, 2·min(P(X ≤ obs), P(X ≥ obs)))`.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit with the message "Add the VisA statistics".

### Task 4: Product-line harness and fidelity gate

**Files:**
- Modify: `mizan/visa_evaluation.py`
- Test: `tests/test_visa_evaluation.py`

**Interfaces:**
- Consumes: Task 2 `protocol_split` and Task 3 statistics.
- Produces:
  - `Scorer = NamedTuple("Scorer", [("name", str), ("features", Callable), ("engine_path", bool)])`.
    - Engine-path scorers receive the decoded 96 px greyscale `PIL.Image`.
    - `dinov2-native` receives `Image.open(path)` of the original.
  - `product_line(reference, calibration, tests, scorer, *, rules) -> dict`.
    - `reference` and `calibration` are lists of paths, and `tests` is a list of `(path, label)`.
    - Engine-path scorers build banks and calibration scores through `b.fit([{"group": path, "png": engine_png(path)}, ...], ..., scorer.features, profile)`.
    - `dinov2-native` computes features from originals, stacks the bank, and sorts the calibration with `b.score`.
    - Decisions always come from `b.rank_decision`.
    - Returns `bank_digest, m, rows: [{path, label, score, p_value, flag_by_rule, refused}], scores_other_bank` (per-image scores against the As shipped bank).
  - `fixture_gate(product_line_fn) -> dict` and `dinov2_gate(record: dict, shipped: dict) -> dict`, each with keys `passed, checked, mismatches, bit_exact`.
    - `fixture_gate` feeds the shipped generated split through `product_line`: reference = `examples.inspection()["reference"]`, calibration = seeds 100–123, frozen test = seeds 9100–9179. It compares with `reports/evaluation/basira.json` under the gate rules.

- [ ] **Step 1: Write the failing tests:**
  - `test_fixture_gate_passes_with_shipped_descriptor`: `fixture_gate(...)["passed"]`, with mismatches equal to 0 and every frozen row compared.
  - `test_fixture_gate_fails_when_one_p_value_differs`: patch `b.rank_decision` to alter one p-value. `passed` is False and the mismatch names the row.
  - `test_dinov2_gate_tolerance`, using synthetic records:
    - an equal record passes with `bit_exact` True;
    - a score off by 1e-9 relative passes with `bit_exact` False;
    - a flipped flag fails.
  - `test_native_scorer_never_uses_engine_decode`: a spy on `b.decode_image` during a stand-in `product_line` with `engine_path=False` records zero calls.
  - `test_query_leak_is_refused_not_scored`: a test path byte-identical to a reference image gives `refused == "QUERY_LEAK"` and no flag.
  - `test_features_computed_once_per_image`: a spy feature function on the stand-in is called once per reference, calibration and test image. Calibration images are counted by `b.fit`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. To score each test image once against both banks, compute its features once and call `b.score` with each bank.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit with the message "Add the VisA product-line harness and fidelity gate".

### Task 5: Study, examples and the full record

**Files:**
- Modify: `mizan/visa_evaluation.py`
- Test: `tests/test_visa_evaluation.py`

**Interfaces:**
- Produces:
  - `study(line: dict, draw_scores, eval_scores, anomaly_scores, *, draws=400, sizes=(24,50,100), seed=23) -> dict`. It returns rows per m with `mean_false_alert, share_above_alpha, theoretical_share, mean_detection, draws`, plus `capped` notes.
  - `pick_examples(rows_by_class: dict) -> dict`, with keys `caught, missed, false_alert, passed`. Each value is `{class, path, score}` or `None`, following the Global Constraints rule.
  - `evaluate_visa(root, manifest, scorers, *, quick=False) -> dict`. It returns the evidence record, minus `gate`, `repeat_run` and `environment`:
    - `identity`, `settings` (= `PROTOCOL`), `notes`;
    - `per_class[scorer][config]`: `false_alert`, `detection`, each with x, n and ci95, plus `mode`, `refused`;
    - `pooled[scorer][config]`, with `by_type`;
    - `exchangeability[scorer][config]`, pooled and `by_type`;
    - `auroc[scorer]` (per class, with ci95 and mean);
    - `paired` (the dinov2-engine − descriptor and dinov2-native − dinov2-engine differences in detection, false-alert rate and AUROC);
    - `study[scorer]`;
    - `examples` (`descriptor`, Full marginal, base64 JPEG 96 px greyscale, quality 85);
    - `rows` (per image: path, class, label, scores per scorer for both banks, decisions per configuration).

- [ ] **Step 1: Write the failing tests** on the stand-in, with a stub scorer:
  - `test_study_caps_m_by_pool_and_notes_it`: a draw pool of 30 with sizes (24, 50) gives a row for 24 and a `capped` note for 50.
  - `test_examples_follow_the_rule`: a constructed `rows_by_class` with known scores picks the lower-median items. An empty set gives `None`.
  - `test_record_shape_and_no_test_leak`: `evaluate_visa(..., quick=True)` has every key above. Every test path appears exactly once in `rows`, and no test path is inside `reference`, `calibration` or the pools.
  - `test_pooled_rates_equal_sums_of_classes`: the pooled x and n equal the sums of the per-class x and n.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `quick` shrinks only the draws and sizes for tests, and it never writes.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit with the message "Assemble the VisA evidence record".

### Task 6: Runner, dry run and the real run

**Files:**
- Create: `scripts/evaluate_visa.py`, `reports/visa/basira.json` (real), `reports/visa/runs.json`
- Test: `tests/test_visa_evaluation.py`

**Interfaces:**
- Consumes:
  - `evaluate_visa`, `fixture_gate` and `dinov2_gate`;
  - `models.dinov2_features(root)` and `models.model_identity`;
  - `scripts/evaluate_hf.py`'s staged atomic writer (import it; do not copy it).
- Produces:
  - `run(root, out, *, models_root, repeat=False, dry_run=False, reason: str, seal: str | None = None, loaders=None, quick=False)`. The CLI is `--data var/visa --models var/hf --out DIR --reason TEXT [--seal SHA] [--repeat] [--dry-run]`, and `--out` is required.
  - `reports/visa` without `--repeat` is refused with `EVIDENCE_OUT`.
  - The fixture gate and the DINOv2 gate run before any VisA image is read; a failure exits 2 and writes nothing.
  - `--dry-run` scores train normals only, prints timings and peak RSS, and appends a `dry_run: true` entry to `runs.json`. It never writes `basira.json`.
  - A real run appends to `runs.json` and writes `basira.json`, which adds `gate`, `repeat_run` (identical or not, with differing leaves), `environment` (as in 3.1, plus Pillow), `protocol_digest`, `seal_commit` (from `--seal`, required for a real run) and `elapsed_seconds`.

- [ ] **Step 1: Write the failing tests** (torch-free, with stub loaders and the stand-in):
  - `test_dry_run_never_opens_test_images`: an `open` spy shows no path whose split is test.
  - `test_failed_gate_writes_nothing_and_exits_2`.
  - `test_evidence_dir_needs_repeat`.
  - `test_real_run_requires_seal_and_matching_digest`: a missing `--seal`, or a `PROTOCOL` whose digest ≠ `SEALED_DIGEST`, is refused.
  - `test_run_log_appends`: two dry runs leave two entries, each with commit, date, reason and `dry_run`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS. Commit with the message "Add the VisA runner".
- [ ] **Step 5: Dry run.** Run `MIZAN_HF_MODELS=var/hf .venv-hf/bin/python scripts/evaluate_visa.py --data var/visa --models var/hf --out /tmp/visa-dry --dry-run --reason "timing and memory, train normals only"`. Record the time per scorer and the peak RSS in the task report. Commit `reports/visa/runs.json` with the message "Log the VisA dry run".
- [ ] **Step 6: Real run.** Run `MIZAN_HF_MODELS=var/hf .venv-hf/bin/python scripts/evaluate_visa.py --data var/visa --models var/hf --out reports/visa --repeat --seal <Task 2 SHA> --reason "sealed protocol, first real run"`. Expected: both gates pass, the repeat run is identical, and `basira.json` is written. Report the headline numbers exactly as stored.
- [ ] **Step 7:** Commit `reports/visa/basira.json reports/visa/runs.json` with the message "Evaluate BASIRA on VisA".

### Task 7: Results pages

**Files:**
- Create: `scripts/build_visa_report.py`, `reports/visa/index.html`, `reports/visa/classes.html`
- Modify: `scripts/build_site.py` (`COPIED`: serve them as `report/visa/index.html` and `report/visa/classes.html`), `scripts/check_exhibits.py` (contracts)
- Test: `tests/test_evaluation.py` (a new `VisaPageTests` class)

**Interfaces:**
- Consumes: `reports/visa/basira.json`, and `scripts/build_report.py`'s shared style, helpers and font embedding (import them, don't copy).
- Produces:
  - `build(source=ROOT/"reports/visa", out_dir=ROOT/"reports/visa") -> list[Path]`.
  - **Summary page** (`index.html`): the pooled headline per scorer and configuration with Clopper–Pearson intervals; the exchangeability verdicts, pooled and by type; AUROC means with intervals; the paired comparisons, with one sentence saying the intervals are not adjusted for multiple comparisons; the example strip; attribution; and a link to `classes.html`.
  - **Classes page** (`classes.html`): per-class tables grouped by type; the study with "no interval is reported for these shares"; and a back link.
  - Every direction word and verdict is computed from interval signs or `verdict`.

- [ ] **Step 1: Write the failing tests:**
  - Both pages build. Each is under 60,000 characters, has no Arabic codepoints, and has no `http` loads other than the licence link text.
  - The embedded data equals the source values for each scorer's pooled detection and false-alert counts.
  - The attribution text appears verbatim.
  - The summary page shows four example images or the word "none" for each empty set.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Add contracts to `check_exhibits.py`:
  - zero external requests;
  - no horizontal overflow at 390 px;
  - `.fit` tables fit;
  - each Reading or verdict cell agrees with the stored interval or verdict;
  - the contrast check covers the new pages.
- [ ] **Step 4:** Run `.venv/bin/python scripts/build_visa_report.py`, then `.venv/bin/python scripts/build_site.py`, then `.venv/bin/python scripts/check_exhibits.py --chromium <path> --site _site --out /tmp/b.json`. Expected: PASS. Run `node scripts/check_exhibit_logic.mjs`. Expected: PASS. Save headless screenshots of both pages at 1280 and 390 px into the SDD folder.
- [ ] **Step 5:** Run the full suite, then commit with the message "Show BASIRA on VisA".

### Task 8: Landing, docs and records

**Files:**
- Modify:
  - `scripts/build_site.py`, `site/landing.html`;
  - `README.md`, `README.ar.md`, `CHANGELOG.md`;
  - `docs/QUALIFICATION.md`, `docs/TECHNICAL_GUIDE.md`;
  - `CLAUDE.md` (add the fetch and evaluate commands to the Commands block; nothing else);
  - `tests/test_site.py`;
  - `reports/application-tests.txt`, `reports/browser-checks.json`, `SHA256SUMS`.
- Create: `docs/RELEASE_3.2.md`, `docs/DATA_LICENSES.md`

**Interfaces:**
- Produces `facts()` keys, each read from `reports/visa/basira.json` with guards that fail closed:
  - `visa_classes`, `visa_test_normals`, `visa_test_anomalies`;
  - `visa_desc_detect`, `visa_desc_detect_lo`, `visa_desc_detect_hi`, `visa_desc_fa`, `visa_desc_fa_lo`, `visa_desc_fa_hi` (`descriptor`, Full marginal);
  - `visa_native_detect`, `visa_native_detect_lo`, `visa_native_detect_hi`;
  - `visa_exch_en` and `visa_exch_ar` (the verdict for `descriptor`, Full marginal);
  - `visa_auroc_desc` and `visa_auroc_native` (with `_lo` and `_hi`).
- Produces a landing block, "Real images: BASIRA on VisA", in English and Arabic. It uses only these keys, links to `report/visa/`, and gives the attribution in one line.

- [ ] **Step 1: Write the failing tests** in `tests/test_site.py`:
  - the keys exist and equal values computed from the JSON;
  - each guard fails closed when its source value is removed or flipped (a subTest per guard);
  - the block appears in both languages.
- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run. Expected: PASS.
- [ ] **Step 3: Docs.** Write and update the documentation:
  - `docs/RELEASE_3.2.md` covers:
    - the protocol and the seal commit;
    - the gate results;
    - the results, with numbers from `reports/visa/`;
    - the exchangeability reading;
    - limits: one dataset, CPU-only, 96 px greyscale engine front end, no localisation metrics, no escape-rate certificate;
    - attribution.
  - `docs/DATA_LICENSES.md` holds the attribution.
  - Update the README and README.ar rows. Replace README's "All inputs are generated fixtures" with wording that names VisA as the one real dataset.
  - Add a 3.2.0 (unreleased) entry to the CHANGELOG.
  - Add a QUALIFICATION row for BASIRA real-image evidence.
  - Add a TECHNICAL_GUIDE section with the fetch and evaluate recipe, time, peak RSS and disk, as measured in Task 6.
  - Add the two commands to the CLAUDE.md Commands block.
- [ ] **Step 4: Records.** Run the full suite with `-v` in `.venv`, the 4 loader tests in `.venv-hf`, and the suite on Python 3.13. Rewrite `reports/application-tests.txt` in its header format, since `build_site.recorded_tests` parses it. Regenerate `reports/browser-checks.json`. Regenerate `SHA256SUMS` last (every tracked file except itself, C-sorted), then run `sha256sum -c SHA256SUMS`. Expected: all OK.
- [ ] **Step 5:** Commit with the message "Publish the VisA evaluation on the site and in the docs".
