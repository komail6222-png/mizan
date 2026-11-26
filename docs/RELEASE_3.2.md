# MIZAN 3.2 (unreleased) | BASIRA on real images

Prepared 26 November 2026 on the `visa-evaluation` branch; not yet released. Builds on 3.1.

Until 3.1, every BASIRA figure came from generated rings. 3.2 runs the unchanged engine on real photographs, the public VisA dataset, and asks two questions: **does the false-alert promise of BASIRA's calibration hold on real images, and how many real defects does it catch at that promise?** The answers are published as they came out. The promise held in every check made. Detection is low: at the 5% target with the full bank, the shipped descriptor catches 26.8% of defective images and DINOv2-small, at its native colour input, 38.6%. Most defects pass. The deployed engine, its profiles, the signed exhibit and the pinned key are unchanged.

All figures below are in `reports/visa/basira.json`. The run log is `reports/visa/runs.json`.

## 1. Data

VisA (Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and Segmentation", arXiv:2207.14315), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. These are real public images, not MIZAN's own data. Licence, source and changes: [`docs/DATA_LICENSES.md`](DATA_LICENSES.md).

- **Source.** The archive `VisA_20220922.tar` (1,929,840,640 bytes, SHA-256 `2eb8690c…f40f362`) and the official one-class split `split_csv/1cls.csv` from `amazon-science/spot-diff` at commit `2a692ab5` (SHA-256 `a48557e6…0332995`). `scripts/fetch_visa.py` downloads both into the git-ignored `var/visa/` (16 resumable byte ranges, then a SHA-256 check) and verifies all 10,821 images against `reports/visa/manifest.json`.
- **Product lines.** Each of the 12 classes is treated as one customer's product line with its own reference bank and calibration set: candle, capsules, macaroni1 and macaroni2 (multiple instances); cashew, chewinggum, fryum and pipe_fryum (single instance); pcb1 to pcb4 (complex structure).
- **Test set.** The official test split, untouched: 962 normal and 1,200 defective images (100 per class). No image was refused as a duplicate of a reference or calibration part, and no training image was dropped as a duplicate.
- **What each scorer sees.** BASIRA's engine path converts each image to greyscale, resizes it to 96×96 and submits it as PNG through `basira.engine.decode_image`. Two scorers use that path: the shipped hand-built descriptor and DINOv2-small (`facebook/dinov2-small`, revision `ed25f3a3`, the 3.1 pin). A third, DINOv2-small at native input, sees the original colour image and is off the engine path.

## 2. A sealed protocol

The whole protocol (split sizes, configurations, rules, scorers, statistics, examples and gate) is the dictionary `mizan.visa.PROTOCOL`, whose digest `2175caee…370e705a` is fixed in code. It was sealed in commit `13827ee6` before any test image was scored. A real run refuses a protocol whose digest has changed and must name the seal commit.

- **Split per class.** Train normals in path order, then a permutation seeded from SHA-256 of `20220922:<class>`: 30 reference parts, 100 calibration parts, and the rest halved into a draw pool and an evaluation pool for the repeated-calibration study.
- **Configurations.** *As shipped*: 10 reference and 24 calibration parts, the marginal rule. *Full*: 30 reference and 100 calibration parts, the marginal rule and the PAC rule (δ = 1/10). α = 1/20 throughout.
- **Statistics.** Clopper–Pearson 95% intervals for rates. An exact exchangeability check: the long-run false-alert rate of a rank-k-of-m threshold is Beta(m−k+1, k), so each class's false-alert count is Beta-Binomial, and the classes combine by exact convolution into a central 95% range. AUROC by Mann–Whitney with a stratified bootstrap. Paired comparisons by a bootstrap stratified by class and label (2,000 draws, seed 2026). The study draws 400 banks per size m = 24, 50, 100 (seed 23). Many intervals are reported and none is adjusted for multiple comparisons.
- **Dry run first.** Before the real run, a dry run scored train normals only, for timing and memory (1,825.1 s, peak 588.1 MiB). It opened no test image: it verified and read the train entries only.

## 3. The fidelity gate

Before any VisA image is scored, the new harness must reproduce published results, and a mismatch stops the run with exit code 2 and writes nothing. It passed:

| Gate | Checks | Result |
|---|---|---|
| The shipped descriptor reproduces the frozen test of `reports/evaluation/basira.json` (bank digest, m, every item's seed, condition, p-value and flag) | 323 | exact |
| DINOv2-small reproduces 3.1's frozen test of `reports/hf/basira.json` (decisions exact, scores within 1e-6 relative) | 404 | bit-exact |

The whole evaluation then ran a second time in a fresh Python process and matched the first with no differing value. One real run was made, from commit `eb23ddf8` with no tracked file changed, in 4,562.9 s including the repeat (peak resident memory 594.6 MiB).

## 4. Results

### Does the false-alert promise hold?

Split-conformal calibration promises a false-alert rate of at most α on average over calibration banks, if the test normals are exchangeable with the calibration normals. The check compares each observed count with the exact distribution that assumption implies.

| Scorer | Full bank, marginal rule: false alerts | Exact central 95% range | Two-sided p |
|---|---|---|---|
| Shipped descriptor | 54 of 962 | 31 to 67 | 0.503 |
| DINOv2-small, engine input | 59 of 962 | 31 to 67 | 0.240 |
| DINOv2-small, native input | 48 of 962 | 31 to 67 | 0.975 |

All 9 pooled checks (three scorers, three rules) and all 27 checks by VisA type read *consistent with exchangeability*. On these images, no check detects a departure from the assumption the promise rests on.

### How many defects are caught?

Pooled over the 12 lines, with exact 95% intervals:

| Scorer | Configuration | Defects caught | False alerts |
|---|---|---|---|
| Shipped descriptor | As shipped (10, 24) | 248 of 1,200 · 20.7% (18.4% to 23.1%) | 41 of 962 · 4.3% (3.1% to 5.7%) |
| | Full (30, 100) | 322 of 1,200 · 26.8% (24.3% to 29.4%) | 54 of 962 · 5.6% (4.2% to 7.3%) |
| | Full, PAC rule | 197 of 1,200 · 16.4% (14.4% to 18.6%) | 22 of 962 · 2.3% (1.4% to 3.4%) |
| DINOv2-small, engine input | As shipped | 324 of 1,200 · 27.0% (24.5% to 29.6%) | 35 of 962 · 3.6% (2.5% to 5.0%) |
| | Full | 436 of 1,200 · 36.3% (33.6% to 39.1%) | 59 of 962 · 6.1% (4.7% to 7.8%) |
| | Full, PAC rule | 243 of 1,200 · 20.3% (18.0% to 22.6%) | 19 of 962 · 2.0% (1.2% to 3.1%) |
| DINOv2-small, native input | As shipped | 356 of 1,200 · 29.7% (27.1% to 32.3%) | 43 of 962 · 4.5% (3.3% to 6.0%) |
| | Full | 463 of 1,200 · 38.6% (35.8% to 41.4%) | 48 of 962 · 5.0% (3.7% to 6.6%) |
| | Full, PAC rule | 281 of 1,200 · 23.4% (21.0% to 25.9%) | 15 of 962 · 1.6% (0.9% to 2.6%) |

At a 5% false-alert target, most real defects pass every scorer. The PAC rule trades detection for a per-bank bound.

### Ranking quality

Mean AUROC over the 12 lines, which depends on no threshold: shipped descriptor 0.717 (0.696 to 0.736), DINOv2-small on the engine input 0.799 (0.781 to 0.816), at native input 0.831 (0.814 to 0.847). The lines differ widely. The descriptor ranks capsules no better than chance (0.481, interval 0.385 to 0.575) and pcb4 best of its lines (0.944, 0.911 to 0.973). DINOv2-small is not ahead everywhere: on pcb4 it reaches 0.756 on the engine input and 0.877 at native input. These per-class figures have no paired interval.

### Scorer against scorer, on the same images

Full bank, marginal rule; paired bootstrap stratified by class and label:

| Comparison | Defects caught | False alerts | Mean AUROC |
|---|---|---|---|
| DINOv2-small on the engine input minus the descriptor | +9.5 points (+6.8 to +12.1), resolved | +0.5 points (−1.2 to +2.5), not resolved | +0.082 (+0.060 to +0.104), resolved |
| DINOv2-small at native input minus on the engine input | +2.3 points (+0.2 to +4.5), resolved | −1.1 points (−2.4 to 0.0), not resolved | +0.032 (+0.020 to +0.045), resolved |

On the engine's own 96 px greyscale input, the pretrained features catch more defects than the hand-built descriptor at the same promise. Native colour input adds a smaller gain on top. It is off the engine path, and this evaluation does not separate the effect of resolution from that of colour.

### Repeated calibration

With each line's full bank fixed, 400 calibration sets per size were drawn from its draw pool. Long-run false alerts were measured on its evaluation pool, and detection on its test anomalies. Pooled over lines with equal weight, the mean false-alert rate stays below 5% at every size for every scorer (from 3.1% to 4.6%). The share of banks above 5% is below its theoretical value in every row; at m = 100 it is 26.4%, 37.8% and 33.9% against a theoretical 43.6%. Each line's draws reuse one draw pool and one evaluation pool, so these shares are indicative: no interval is reported for them.

### Examples

The results page shows four images, chosen by a fixed rule (shipped descriptor, full bank, marginal rule, the lower-median score of each outcome): a caught defect (pcb4), a missed defect (capsules), a false alert (capsules) and a passed normal part (pcb1). Each is shown as the engine sees it, 96×96 greyscale, with the attribution.

## 5. Pages and site

- `reports/visa/index.html` (summary) and `reports/visa/classes.html` (per line, by type, the study) are built by `scripts/build_visa_report.py`, which refuses a record whose gate failed or whose repeat run was not identical. They are served at `report/visa/`, and the evaluation report links to them.
- The introduction gains a bilingual block, "Real images: BASIRA on VisA". `scripts/build_site.py` reads its figures from `reports/visa/basira.json`. It stops the build if a sentence no longer holds: the run must be the sealed protocol with a passing gate and an identical repeat; every scorer must be scored on the same images; most defects must still pass; each step towards DINOv2 at native input must still be a resolved gain in detection and AUROC, with neither false-alert difference resolved; and every reading must match the sign of its interval. The introduction and its limits no longer say that all data is generated.
- `scripts/check_exhibits.py` checks both pages in Chromium at 1280 and 390 px. The readings must follow the stored verdicts and interval signs, with four examples or "none", no overflow, comparison tables that fit, zero external requests, and WCAG AA contrast in light and dark.

## Disclosures

- **The seal fixes the protocol, not the code.** The evaluation harness and its runner were finished after the seal commit and before the real run. Two changes in that interval are worth naming. The download became 16 parallel resumable byte ranges, verified by the same SHA-256, after a single-stream download was cut off when the machine slept. The run log now records the tree state at the start of a run.
- **The dry run's log entry reads `"dirty": true`.** That flag came from the earlier check, which counted untracked output files. The dry run ran from commit `c990e2f9` with the evaluation code committed. The real run's entry and record read `"dirty": false`.
- **After the real run, no engine, harness or protocol code changed.** Only presentation code changed. One paired interval's upper end is exactly 0.0, so a reading that said "spans zero" now says "includes zero".

## Limits

- **One public dataset**, 12 product types, image-level labels only. There are no localisation metrics (pixel AUROC, PRO).
- **No escape-rate or industrial certificate.** VisA's defects are not a production line's defect mix, and a detection rate on them does not carry over to one. The false-alert check tests exchangeability between VisA's own normal images. It does not test drift on a live line.
- **The engine's front end is 96 px greyscale.** It discards colour and fine detail. The results above say how much that costs on these images, not how to fix it.
- **CPU only**, DINOv2-small frozen, no fine-tuning.
- **Many intervals, no multiplicity adjustment.** The study's shares and the per-class figures are indicative.

## Reproduction

The recipe, timings, memory and disk use are in [`docs/TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md#basira-on-real-images-reportsvisa).
