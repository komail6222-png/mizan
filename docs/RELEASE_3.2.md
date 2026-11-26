# MIZAN 3.2 (unreleased) | Real data: BASIRA on images, RASID on load

Prepared 26 November 2026; not yet released. Builds on 3.1. 3.2 has two parts:
- **Part 1** (below, merged from the `visa-evaluation` branch) runs BASIRA on real photographs, the public VisA dataset.
- **Part 2** ([RASID on real load](#part-2--rasid-on-real-load-opsd), from the `rasid-opsd` branch) runs RASID on the real load and solar of two business sites from the Open Power System Data household package.

Both parts keep the same discipline:
- the protocol is sealed in code before the real data is scored;
- a fidelity gate on the published results must pass first;
- the whole run is repeated in a second process;
- every figure is published with its interval.

## Part 1 · BASIRA on real images (VisA)

Until 3.1, every BASIRA figure came from generated rings. 3.2 runs the unchanged engine on real photographs, the public VisA dataset, and asks two questions: **does the false-alert promise of BASIRA's calibration hold on real images, and how many real defects does it catch at that promise?** The answers are published as they came out. No check found a departure from the promise, though the check flags only clear departures. Detection is low: at the 5% target with the full bank, the shipped descriptor catches 26.8% of defective images and DINOv2-small, at its native colour input, 38.6%. Most defects pass. The deployed engine, its profiles, the signed exhibit and the pinned key are unchanged.

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
- **Statistics.** Clopper–Pearson 95% intervals for rates. An exact exchangeability check: the long-run false-alert rate of a rank-k-of-m threshold is Beta(m−k+1, k), so each class's false-alert count is Beta-Binomial, and the classes combine by exact convolution into a central 95% range. AUROC by Mann–Whitney with a stratified bootstrap. Paired comparisons by a bootstrap stratified by class and label (2,000 draws, seed 2026). The study draws 400 calibration sets per size m = 24, 50, 100 (seed 23), with each line's bank fixed. Many intervals are reported and none is adjusted for multiple comparisons.
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

All 9 pooled checks (three scorers, three rules) and all 27 checks by VisA type read *consistent with exchangeability*. On these images, no check detects a departure from the assumption the promise rests on. The check flags only clear departures: with the full bank, any count from 31 to 67 out of 962 reads consistent.

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

At a 5% false-alert target, most real defects pass every scorer. The PAC rule trades detection for a per-bank bound. Each false-alert interval in this table describes these 12 fitted thresholds only, one per line; whether the promise holds is the exchangeability check above.

### Ranking quality

Mean AUROC over the 12 lines, which depends on no threshold: shipped descriptor 0.717 (0.696 to 0.736), DINOv2-small on the engine input 0.799 (0.781 to 0.816), at native input 0.831 (0.814 to 0.847). The lines differ widely. The descriptor ranks capsules no better than chance (0.481, interval 0.385 to 0.575) and pcb4 best of its lines (0.944, 0.911 to 0.973). DINOv2-small is not ahead everywhere: on pcb4 it reaches 0.756 on the engine input and 0.877 at native input. These per-class figures have no paired interval.

### Scorer against scorer, on the same images

Full bank, marginal rule; paired bootstrap stratified by class and label:

| Comparison | Defects caught | False alerts | Mean AUROC |
|---|---|---|---|
| DINOv2-small on the engine input minus the descriptor | +9.5 points (+6.8 to +12.1), resolved | +0.5 points (−1.2 to +2.5), not resolved | +0.082 (+0.060 to +0.104), resolved |
| DINOv2-small at native input minus on the engine input | +2.3 points (+0.2 to +4.5), resolved | −1.1 points (−2.4 to 0.0), not resolved | +0.032 (+0.020 to +0.045), resolved |

On the engine's own 96 px greyscale input, the pretrained features catch more defects than the hand-built descriptor at the same promise. Native colour input adds a smaller gain on top. It is off the engine path, and this evaluation does not separate the effect of resolution from that of colour.

These intervals resample test images with each line's calibration set fixed, so "resolved" holds for those 12 sets. The native-input gain in detection is the narrowest: its interval starts at +0.2 points, two images in 1,200. The repeated-calibration study, which redraws the calibration sets, points the same way: at m = 100, DINOv2-small catches 38.9% at native input against 35.8% on the engine input, a mean over 400 draws per line with no interval.

### Repeated calibration

With each line's full bank fixed, 400 calibration sets per size were drawn from its draw pool. Long-run false alerts were measured on its evaluation pool, and detection on its test anomalies. Pooled over lines with equal weight, the mean false-alert rate stays below 5% at every size for every scorer (from 3.1% to 4.6%). The share of calibration sets above 5% is below its theoretical value in every row; at m = 100 it is 26.4%, 37.8% and 33.9% against a theoretical 43.6%. Each line's draws reuse one draw pool and one evaluation pool, so these shares are indicative: no interval is reported for them.

### Examples

The results page shows four images, chosen by a fixed rule (shipped descriptor, full bank, marginal rule, the lower-median score of each outcome): a caught defect (pcb4), a missed defect (capsules), a false alert (capsules) and a passed normal part (pcb1). Each is shown as the engine sees it, 96×96 greyscale, with the attribution.

## 5. Pages and site

- `reports/visa/index.html` (summary) and `reports/visa/classes.html` (per line, by type, the study) are built by `scripts/build_visa_report.py`, which refuses a record whose gate failed or whose repeat run was not identical. They are served at `report/visa/`, and the evaluation report links to them.
- The introduction gains a bilingual block, "Real images: BASIRA on VisA". `scripts/build_site.py` reads its figures from `reports/visa/basira.json`. It stops the build if a sentence no longer holds: the run must be the sealed protocol with a passing gate and an identical repeat; every scorer must be scored on the same images; most defects must still pass; each step towards DINOv2 at native input must still be a resolved gain in detection and AUROC, with neither false-alert difference resolved; and every reading must match the sign of its interval. The introduction and its limits no longer say that all data is generated.
- `scripts/check_exhibits.py` checks both pages in Chromium at 1280 and 390 px. Each reading on the page must match the verdict or reading stored in the record, which the evaluation derives from the exact ranges and interval signs. It also checks four examples or "none", no overflow, comparison tables that fit, zero external requests, and WCAG AA contrast in light and dark.
- The summary page labels the pooled false-alert interval as describing these 12 fitted thresholds only, so it is not read as the test of the promise. The pages read the protocol's numbers (bank sizes, α, δ, draws) from the record.

## Disclosures

- **The seal fixes the protocol, not the code.** The evaluation harness and its runner were finished after the seal commit and before the real run. Two changes in that interval are worth naming. The download became 16 parallel resumable byte ranges, verified by the same SHA-256, after a single-stream download was cut off when the machine slept. The run log now records the tree state at the start of a run.
- **The dry run's log entry reads `"dirty": true`.** That flag came from the earlier check, which counted untracked output files. The dry run ran from commit `c990e2f9` with the evaluation code committed. The real run's entry and record read `"dirty": false`.
- **After the real run, no engine, harness (`mizan/visa_evaluation.py`) or protocol code changed.** The final review led to changes that alter no computation:
  - **The runner** (`scripts/evaluate_visa.py`):
    - `--child` now runs only as the repeat run that `--repeat` starts, and only into the folder its parent names.
    - The run log gains a "started" entry, written before any VisA image is scored. The two entries already in `reports/visa/runs.json` have no `event` field and were written at the end of their runs.
    - `--seal` must be the commit that introduced the digest.
    - The tree-state check also counts untracked files under `mizan/` and `scripts/`.
    - A full run needs all 12 classes.
  - **The download** (`scripts/fetch_visa.py`): range files name their bytes, and each reply's `Content-Range` is checked.
  - **The presentation:**
    - One paired interval's upper end is exactly 0.0, so a reading that said "spans zero" now says "includes zero".
    - The false-alert interval is labelled as describing these 12 fitted thresholds only, as the design asked.

## Limits

- **One public dataset**, 12 product types, image-level labels only. There are no localisation metrics (pixel AUROC, PRO).
- **No escape-rate or industrial certificate.** VisA's defects are not a production line's defect mix, and a detection rate on them does not carry over to one. The false-alert check tests exchangeability between VisA's own normal images. It does not test drift on a live line.
- **The engine's front end is 96 px greyscale.** It discards colour and fine detail. The results above say how much that costs on these images, not how to fix it.
- **CPU only**, DINOv2-small frozen, no fine-tuning.
- **Many intervals, no multiplicity adjustment.** The study's shares and the per-class figures are indicative. The paired readings hold for each line's one calibration set.

## Reproduction

The recipe, timings, memory and disk use are in [`docs/TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md#basira-on-real-images-reportsvisa).

## Part 2 · RASID on real load (OPSD)

Until now, every RASID figure came from a generated commercial site over 60 days, and 3.0 stated the limit: 60 generated days cannot settle the forecast's value under RASID's illustrative tariff. Part 2 runs RASID's unchanged forecaster, planner and independent verifier on two real business sites. It measures how well each policy plans, **without currency**: the owner chose this over euros, which would need a real tariff and a battery cost. All figures below are in `reports/opsd/rasid.json`; the run log is `reports/opsd/runs.json`.

**The answers, as they came out:**
- **Planned and verified:** 811 real days were planned, and the independent verifier accepted every plan, 5,677 of 5,677.
- **Forecasting:** on this real load, the shipped ridge forecaster's error is not resolved from that of a seasonal baseline that repeats last week's load, at either site. Chronos-Bolt-tiny, the 3.1 pin used zero-shot, cuts the error from 19.4% to 14.0% of mean load (both sites weighted equally), a resolved difference.
- **Planning:** planning on Chronos's quantiles without the risk weight captures 58.0% of the battery's achievable value (95% interval 56.3% to 61.1%). The shipped risk-aware ridge plan captures 24.9% (22.7% to 29.7%), and costs more than simply planning on the seasonal baseline, a resolved difference.

The engine, its settings and the signed exhibit are unchanged.

### Data

Open Power System Data, *Data Package Household Data*, version 2020-04-15, CC BY 4.0, with primary data from the CoSSMic project in Konstanz, Germany. Licence, source and changes: [`docs/DATA_LICENSES.md`](DATA_LICENSES.md).
- **The file:** the 60-minute file (15,654,559 bytes, SHA-256 `15956440…428e97d60`), downloaded by `scripts/fetch_opsd.py` into the git-ignored `var/opsd/` and refused unless both values match.
- **The two business sites:** a warehouse (`industrial1`) and a research-institute building (`industrial3`). The homes, schools and a crafts business in the package were inspected and set aside, because of small size, missing solar, short coverage, their own battery or unmetered export.
- **Reconstructing load:** the values are cumulative meter readings, each the reading at the end of the hour its row is labelled with. Solar at both sites peaks around Konstanz's solar noon only under that reading. Load is reconstructed as grid import plus solar. Neither site has an export meter, so this holds only if neither ever exports. A gate checks that: no sunny hour at either site has grid import near zero (0 of 3,432 and 0 of 2,622).
- **What was looked at before the seal:** coverage, flags, filled days, export, alignment and day counts. No forecast, plan or cost was computed on site data before the seal.

### A sealed protocol

`mizan.opsd.PROTOCOL` holds every choice (spec rules 1–12). Its digest, `e24cc97b…6ad78b`, was sealed in commit `72e51d50` before any site day was forecast, and a run refuses a protocol whose digest has changed.
- **Clean days:** every hour metered, no hour flagged as filled, not a copy of the day before, and no straight-line fill.
- **Planned days:** a 24-hour clean day whose 7 previous days are clean. The two daylight-saving days at each site are excluded and counted, because Chronos forecasts exactly 24 hours.
- **The battery:** RASID's settings scaled by each site's mean load over its first 28 clean days:
  - warehouse: 27.1 kWh and 6.8 kW (scale 0.338);
  - research building: 92.5 kWh and 23.1 kW (scale 1.156).

  The grid limit is set so it never binds. Prices, risk weight and efficiencies are unchanged, and time-of-use prices apply by local hour.
- **Carried state:** each policy carries its charge and month-to-date peak. Both start afresh after any gap.
- **Refusals:** a day where any policy's planner input is refused, no plan is found or a plan is rejected is dropped for every policy, and counted.
- **Intervals:** a bootstrap of 7-day blocks that never span a gap, with 4,000 draws. The seeds are 2026 for the warehouse and 2027 for the research building (seed plus site index); see Disclosures. Pooled results weight both sites equally and are unit-free.

### The fidelity gate

Before any site day, the new day loop ran RASID's generated site and had to reproduce:
- `reports/evaluation/rasid.json`: 253 checks;
- the Chronos side of `reports/hf/rasid.json`: 8 checks.

Both passed with 0 mismatches. One real run was made, from commit `a3f37c3a` with a clean tree. It took 505.9 s including the repeat in a second Python process, which matched with no differing value (peak resident memory 607.2 MiB).

### Days

| | Warehouse | Research building |
|---|---|---|
| Clean days | 601 | 443 |
| Daylight-saving days excluded | 2 | 2 |
| Fewer than 7 clean days before | 52 | 165 |
| Refused by the ridge forecaster (too little history) | 7 | 3 |
| Dropped by a refusal | 2 | 0 |
| **Planned** | **538** in 8 runs | **273** in 19 runs |

The 2 dropped days (6 and 7 August 2016, warehouse) are days where the planner's input check refused a Chronos scenario (`INPUT_RANGE`).

### Forecast accuracy on load

| | Warehouse | Research building |
|---|---|---|
| Mean load | 34.0 kW | 83.0 kW |
| Ridge (shipped) | 6.414 kW · 18.9% | 16.548 kW · 19.9% |
| Seasonal baseline | 6.383 kW · 18.8% | 16.954 kW · 20.4% |
| Chronos median | 4.377 kW · 12.9% | 12.539 kW · 15.1% |
| 80% coverage, ridge / Chronos | 78.0% / 79.0% | 73.8% / 74.5% |

Paired daily differences:
- **Ridge − baseline:** +0.031 kW (−0.490 to +0.402) at the warehouse and −0.405 kW (−1.656 to +0.701) at the research building. Neither is resolved.
- **Chronos − ridge:** −2.037 kW (−2.233 to −1.526) and −4.009 kW (−6.246 to −2.618). Both are resolved.
- **Pooled, as a share of mean load:** ridge − baseline −0.2 points (−1.3 to +0.7), not resolved; Chronos − ridge −5.4 points (−6.6 to −4.1), resolved.

### Planning quality

Share of the battery's achievable value, that is, the part of the hindsight plan's improvement over no battery that a policy captures (95% interval):

| Policy | Warehouse | Research building | Both sites |
|---|---|---|---|
| Ridge, risk-aware (shipped) | 19.3% (16.3% to 24.5%) | 30.5% (25.7% to 37.5%) | 24.9% (22.7% to 29.7%) |
| Ridge, risk-neutral | 29.6% (27.6% to 33.6%) | 39.8% (39.1% to 44.4%) | 34.7% (34.2% to 38.2%) |
| Seasonal baseline | 40.1% (37.5% to 44.7%) | 43.1% (41.8% to 47.3%) | 41.6% (40.5% to 45.0%) |
| Chronos, risk-aware | 35.6% (32.2% to 40.4%) | 41.4% (36.8% to 48.1%) | 38.5% (35.9% to 42.9%) |
| Chronos, risk-neutral | 57.6% (55.3% to 60.3%) | 58.4% (55.4% to 63.8%) | 58.0% (56.3% to 61.1%) |

Paired daily cost differences, pooled as a share of each site's mean daily cost without a battery:

| Comparison | Both sites | Reading |
|---|---|---|
| Ridge risk-aware − seasonal baseline | +1.4% (+1.0% to +1.7%) | costlier, resolved |
| Ridge risk-neutral − seasonal baseline | +0.6% (+0.3% to +0.7%) | costlier, resolved |
| Ridge risk-aware − ridge risk-neutral | +0.8% (+0.5% to +1.1%) | costlier, resolved |
| Chronos risk-neutral − ridge risk-neutral | −1.9% (−2.2% to −1.6%) | cheaper, resolved |
| Chronos risk-neutral − seasonal baseline | −1.4% (−1.7% to −1.0%) | cheaper, resolved |
| Chronos risk-aware − ridge risk-aware | −1.1% (−1.5% to −0.7%) | cheaper, resolved |

Every comparison is resolved in the same direction at each site except one: ridge risk-neutral − seasonal baseline at the research building, +1.228 units a day (−0.304 to +2.317), is not resolved. The page `report/opsd/` lists every per-site figure.

**What this says.**
- **The shipped forecaster:** on real load, the shipped ridge forecaster's error is not resolved from last week's load, and its plans capture less of the battery's value than plans built on that baseline. The risk weight costs more again, a resolved difference as in 3.0.
- **The forecast's value:** 3.0's 60 generated days could not resolve the ridge forecast's value under the illustrative tariff. These real days resolve it for the warehouse and for both sites pooled, and it is negative: risk-neutral plans on the ridge forecast cost more than plans on the seasonal baseline (pooled +0.6% of the daily cost without a battery, 95% interval +0.3% to +0.7%). It is not resolved at the research building.
- **A pretrained forecaster changes the picture:** Chronos is more accurate at both sites, and planning on its quantiles without the risk weight captures the most value of the six forecast-based policies. As in 3.1, the Chronos policies differ from the ridge ones in their scenario rule as well as in the forecaster. The deployed default is still the ridge forecaster with the risk weight. Changing it would be an operator's decision, a signed upgrade, and is not made here.

### Pages and site

- `reports/opsd/index.html`, built by `scripts/build_opsd_report.py` and served at `report/opsd/`. It refuses a record whose gate failed, whose repeat is missing or differs, or that is a quick run.
- The evaluation report links to it, and `scripts/check_exhibits.py` checks it in Chromium: readings match the stored ones, no overflow at 390 px, comparison tables fit, zero external requests, and contrast in light and dark.
- The introduction gains a bilingual block, "Real load: RASID on two business sites". `scripts/build_site.py` reads its figures from the record. It stops the build unless the run is the sealed, gated, second-process repeat of a full run on exactly these two sites, with day counts and readings consistent with the record. Every phrase ("a resolved difference", "costs more than") comes from an interval's sign.

### Disclosures

- **The seal fixes the protocol, not the code.** The day loop, the statistics and the runner were finished after the seal (`72e51d50`) and before the one real run (from `a3f37c3a`).
- **After the real run, no protocol, harness or runner code changed.** Only presentation changed, and a separate check was added (below). The page's "plans verified" first counted the 2 dropped Chronos days as planned; it now counts only plans made, and shows dropped days separately.
- **What was inspected before the seal:** the target-day counts and the data checks above. The clean-day and candidate counts match the sealed run's own exactly (601 and 443 clean days, 547 and 276 candidates). The design's pre-seal sunny-hour counts came from the earlier, one-hour-off alignment and differ from the sealed gate's.
- **Copies of earlier weeks.** Rule 3 drops a day that copies the day before. The package also fills gaps with data from earlier days without always flagging them, and some clean days repeat the day 7 or 14 days earlier exactly, hour by hour.
  - **Warehouse:** 80 clean days, 69 of them planned, 40 with solar identical too. On these days the seasonal baseline sees the actual load.
  - **Research building:** 9 clean days, 4 planned.
  - **A separate check without them:** `scripts/opsd_copy_check.py` removes these days from the sealed run's stored rows and splits runs where they are removed; plans and costs are as run, so this is not a re-planning. Its results are in `reports/opsd/copies.json`.
    - The warehouse's baseline error rises from 6.383 to 7.016 kW, and ridge's from 6.414 to 6.875 kW.
    - Pooled, ridge − baseline becomes −0.5 points of mean load (−1.7 to +0.4), still not resolved.
    - The baseline's pooled share falls from 41.6% to 40.3%.
    - **No reading of the sealed analysis changes.**
  - **For a future protocol:** compare each day with the days one and two weeks earlier too.
- **Bootstrap seeds.** Each site's draws use seed 2026 plus its index (2026 for the warehouse, 2027 for the research building), and the pooled draws reuse them. This was set before the run (ruling R12), but the sealed protocol text and the record's bootstrap labels say "seed 2026" for both sites.
- **Sizing window.** The first 28 clean days that size each battery overlap the first planned days, so sizing is not strictly from before the evaluation. The effect is the same for every policy.

### Limits

- **Two sites in one city, 2015–2017.** This is not evidence about businesses in general.
- **The load is reconstructed** (grid import plus solar). The no-export check covers clean hours at hourly resolution: it found no sunny clean hour with near-zero import.
- **The tariff is RASID's illustrative one,** unchanged. Only the battery is scaled to each site, and it is hypothetical. No currency, bill or payback follows.
- **Within-run blocks.** Blocks never span a gap, so a run's first and last days fall in fewer blocks than its middle days. A percentile interval can therefore sit off its point estimate, more so at the research building, whose runs are short.
- **Meter resolution.** Meters count in steps of a fraction of a kWh, so the rule against four identical hours can also drop a genuinely steady day.
- **CPU only, Chronos-Bolt-tiny zero-shot,** with no tuning.
- **Many intervals,** with no multiplicity adjustment. The share's intervals come from the same block bootstrap.

### Reproduction

The recipe, timings and disk use are in [`docs/TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md#rasid-on-real-load-reportsopsd).
