# BASIRA on real images (VisA): design

Status: draft for the owner's approval (25 November 2026). Phase B, part 1 of the roadmap: move each engine's evaluation from generated fixtures to real public data, one engine at a time, BASIRA first. RASID (on Open Power System Data) follows as part 2. SANAD's real-data work moves into phase C.

## Intent

Every BASIRA number published so far comes from generated rings. The owner wants MIZAN to be a project that companies can actually use and that convinces an AI-engineering reviewer. The most useful next fact is how BASIRA behaves on **real product images with real defects**:

- Does the promised false-alert rate hold when the normal parts are real photographs?
- How many real defects does it catch?
- How does today's hand-built descriptor compare with the pinned DINOv2-small checkpoint from 3.1?

The answer is wanted with 3.1's standards: a fidelity gate, intervals on every comparison, settings fixed before any real result is seen, and no loss hidden.

The framing is **each VisA class as one customer's product line**. A customer brings normal images of its own product, MIZAN builds a reference bank and a calibration set from them, and then decides on new parts. This is how a company would deploy BASIRA, and it is what MIZAN's guarantee speaks to.

## Data

- **Source.** VisA (Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and Segmentation", arXiv:2207.14315).
  - Archive: `https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar`, 1,929,840,640 bytes, Last-Modified 22 Sep 2022, multipart ETag (no published checksum).
  - Official split: `split_csv/1cls.csv` from `github.com/amazon-science/spot-diff` (660,329 bytes; columns `object,split,label,image,mask`).
- **Licence.** CC BY 4.0. Commercial use is allowed, and attribution is required. MVTec AD was rejected because its licence forbids commercial use, which conflicts with the goal.
- **Content.** 12 classes, with 9,621 normal and 1,200 anomalous images. VisA groups them in three types:
  - four PCBs (complex structure);
  - capsules, candles, macaroni1 and macaroni2 (multiple instances; capsules and macaroni2 vary in position and pose);
  - cashew, chewing gum, fryum and pipe fryum (single, roughly aligned objects).
- **Handling.**
  - **Download.** A separate fetch script downloads the archive into `var/visa/`, which git ignores. It records the archive's SHA-256 on first download (trust on first use) and extracts the files.
  - **Manifest.** The script writes a manifest of every image used (relative path, SHA-256, split, label) and commits it under `reports/visa/`, together with the SHA-256 of the archive and of `1cls.csv`.
  - **Offline evaluation.** Evaluation is a different script. It never touches the network and refuses any image whose hash differs from the manifest.
  - **No images in the repository,** except the few small examples allowed under Presentation.
- **Attribution.** The CC BY 4.0 attribution (authors, title, licence link, and a note that images were converted to 96 px greyscale or resized for the model) goes in a new `docs/DATA_LICENSES.md`, on the results page, and in the release notes.

## Protocol (fixed before any VisA result is seen)

Everything in this section is pre-registered. The builder writes these values into the plan and the evidence file's `settings` before the first real run. A change after a real result has been seen is allowed only as a new, separately labelled analysis, and the original stays published.

How real data may be touched *(controller's call)*:

- **Sealed protocol.** The protocol constants (seeds, splits, configurations, scorers and measures) are committed in one "seal" commit before any VisA test image is scored. The evidence names that commit.
- **Before the seal.** The only contact with real data allowed is a dry run on train normals only, for timing and memory. No test image is scored.
- **Run log.** Every real run is listed in a run log inside the evidence: the commit, the date, whether it was a dry run, and the reason.
- **Code defects.** A defect found after a real run, such as a wrong label or path parse, may be fixed only if it is reproduced, covered by a test, and disclosed in the run log with what it changed. That is a code fix, not a protocol change. The protocol constants stay sealed.

### Per class: one product line

The split is the official 1cls split.

**Reference and calibration.** The train normals are shuffled with a fixed seed: one per class, derived from a published base seed and the class name.

- The first **30** are the reference parts. 30 is the most the engine accepts.
- The next **100** are the calibration parts. 100 is likewise the most the engine accepts.
- The rest are split in half by that same shuffle, as 3.1 did with its two pools (pool A and pool B):
  - the **draw pool** supplies the repeated-calibration draws;
  - the **evaluation pool** holds the normals on which the study measures long-run false-alert rates.
  - The official test normals are kept for the main results only. *(Controller's call: about 50–101 test normals per class are too few to measure a long-run rate per class.)*

**Test.** The official test normals and test anomalies, untouched.

**Two pre-registered configurations:**

| Configuration | Reference parts | Calibration parts | Rule |
|---|---|---|---|
| As shipped | the first 10 | the first 24 | marginal, α = 1/20, as in the exhibit |
| Full | all 30 | all 100 | marginal α = 1/20, and PAC with α = 1/20, δ = 1/10 (the minimum m for PAC is 45) |

### Scorers

Every scorer goes through the engine's own `fit`, `score` and `rank_decision`.

| Name | What it sees | Role |
|---|---|---|
| **descriptor** | the engine path: greyscale 96×96 | the shipped hand-built descriptor; the baseline |
| **dinov2-engine** | the same 96×96 greyscale image, through `models.dinov2_features` (as in 3.1) | "Would DINOv2 help as a drop-in descriptor inside today's engine?" |
| **dinov2-native** | the original colour image at 224, through `models.dinov2_features` | "How much does the engine's 96 px greyscale front end cost?" |

**Engine path for real images.** VisA images exceed the engine's input limits (1 MP, 150 KB). For each image the harness therefore produces exactly what the engine would make from the original: convert to greyscale, then a bilinear resize to 96×96. It encodes the result as a PNG and submits it through `encode_image`/`decode_image`. The submitted PNG is the client-side preparation a real customer would need, and it is stated as such.

**dinov2-native.** This scorer uses the same `b.score` and `b.rank_decision` on the same splits, but builds its bank from colour originals outside `decode_image`. It is labelled as off the engine path.

### Measures

Every comparison below gets an interval.

- **Per class, per scorer, per configuration:**
  - The false-alert rate on the official test normals, with a Clopper–Pearson 95% interval.
  - The detection rate on the official test anomalies, with a Clopper–Pearson 95% interval.
  - The decision mode, for example PAC abstaining.
- **Pooled over the 12 classes, per scorer and configuration:**
  - The false-alert rate and the detection rate, with Clopper–Pearson 95% intervals.
  - These rates also broken down by VisA's three types.
- **Guarantee check.** *(Controller's call.)* The check tests exchangeability: whether the real test normals behave like the calibration normals, which is the assumption the false-alert promise rests on. It is not a test of a flat 5%.
  - **Expected value.** Under exchangeability, each rule implies an exact per-class long-run false-alert rate. For the threshold at rank k of m, the rate is distributed Beta(m − k + 1, k). Its mean is 4.0% for As shipped (k = 24 of 24), about 4.95% for Full marginal, and about 2% for the PAC rank.
  - **Exchangeability distribution.** Given n_c test normals in class c, that class's false-alert count is Beta-Binomial(n_c, m − k + 1, k). The pooled count's distribution is the exact convolution over the 12 classes.
  - **Verdict.** The evidence reports the observed pooled count and the central 95% range of that distribution, with the two-sided tail probability. The verdict reads "consistent with exchangeability", "more false alerts than exchangeability allows" or "fewer". The same check is applied per VisA type.
  - **Secondary description.** The plain Clopper–Pearson interval of the pooled rate is also given. It is labelled as describing these 12 fitted thresholds only, so it is not read as the guarantee test.
- **Banks.** *(Controller's call.)* The 30-part Full bank is the primary bank for every measure not tied to a configuration:
  - per-image scores, AUROC and its intervals;
  - the paired comparisons;
  - the repeated-calibration study, which needs m up to 100.

  The As shipped configuration uses its own 10-part bank for its own decisions. Per-image rows carry the scores against both banks.
- **Ranking quality.** Image AUROC per class and scorer, from the same scores, with a stratified bootstrap 95% interval (fixed seed, 2,000 draws), plus the unweighted mean over classes.
- **Paired scorer comparisons on the same images:**
  - dinov2-engine minus descriptor, and dinov2-native minus dinov2-engine.
  - Each is measured as the difference in detection rate and in false-alert rate at the Full marginal rule, and as the difference in AUROC.
  - All use a paired bootstrap stratified by class (fixed seed, 2,000 draws).
  - The direction words "higher", "lower" and "not resolved" follow the interval's sign, as in 3.1.
- **Repeated-calibration study.** This mirrors 3.1's study, with each class's Full bank fixed.
  - Make 400 draws of m calibration parts from that class's draw pool, for m in {24, 50, 100}, capped by the pool size.
  - Measure each draw's long-run false-alert rate on the class's evaluation pool, and its detection rate on the official test anomalies.
  - Report the mean, the share of draws above α, and the exact theoretical share. Give these pooled across classes, and per class marked as indicative.
  - No interval is reported for these shares, and the report says so.
- **Multiplicity.** Many intervals are reported, and none is adjusted. One sentence says so.

### Fidelity gate (before any VisA run)

The new harness is a dataset-agnostic function that takes images, splits and a scorer. Before VisA, a **fixture adapter** feeds it the shipped generated split:

- reference seeds 0–9;
- calibration seeds 100–123;
- frozen test seeds 9100–9179.

The harness must reproduce `reports/evaluation/basira.json`'s frozen test exactly: the bank digest, the calibration m, and every row's seed, condition, p-value and flag.

The gate also re-runs 3.1's DINOv2 frozen test through the same harness, compared against `reports/hf/basira.json`'s model frozen test. *(Controller's call.)*
- **Must match exactly:** calibration m, ranks, p-values, flags and counts.
- **Within a stated tolerance:** float scores, at 1e-6 relative.
- **Exempt:** the DINOv2 bank digest. It hashes float calibration scores, which another CPU's math libraries can move in the last bits.

The evidence records whether the DINOv2 half matched bit for bit, as expected on the machine that produced 3.1, or only within tolerance. This way a stranger on other hardware still gets output.

A failed gate writes nothing and exits 2, as in 3.1. The whole VisA run is repeated in a second process, and the evidence records whether the two runs are identical.

## Outputs

- **Evidence file.** `reports/visa/basira.json`, canonical JSON with these sections:
  - `gate`, `repeat_run`;
  - `identity`: the archive SHA-256, the split CSV SHA-256, the manifest digest and the model pins from `models.PINS`;
  - `environment`, `settings` (every pre-registered value above), `notes` and `elapsed_seconds`;
  - per-class and pooled results for each scorer and configuration;
  - per-image rows: manifest path, label, the score for each scorer, and the decision for each configuration.
  - The file states plainly that this is real public data (VisA), with no claim beyond it: no escape-rate certificate.
- **Manifest.** `reports/visa/manifest.json`.
- **Runner and guards.** `scripts/fetch_visa.py` (network) and `scripts/evaluate_visa.py` (offline). The evaluator has an evidence-directory guard: `reports/visa` is refused without `--repeat`, as in 3.1.
- **CI.** CI stays torch-free and network-free. Its tests use a tiny generated stand-in laid out like VisA in a temporary folder, to cover:
  - the split parser;
  - the manifest and hash refusals;
  - the engine-path preparation;
  - the gate;
  - the write-after-gates behaviour.

## Presentation

- **Results pages.** New pages built by the report builder: "BASIRA on real images (VisA)".
  - **Summary page:** the pooled headline, the guarantee check, the scorer comparison with intervals, the examples and attribution.
  - **Per-class page:** the per-class and per-type tables and the study.

  Each page follows the existing design and contracts, stays under the 60,000-character test with zero external requests, and gets its own browser contract. *(Controller's call: one page would not fit, because the embedded font and shared code alone take about 28,000 characters.)*
- **A few real examples.** The page shows a small strip of real 96 px greyscale examples, chosen by a rule fixed in advance rather than by hand:
  - a caught defect;
  - a missed defect;
  - a false alert;
  - a correct pass.

  They are embedded as data and attributed per CC BY 4.0. They are stored as small JPEGs at 96 px greyscale, a few KB each, to fit the page budget. That makes the result tangible and honest at once.
- **Landing page.** The landing gains one guarded line per headline, with figures read through `build_site.py` and wording decided by interval signs.
- **Docs.** README, README.ar, CHANGELOG (3.2.0, unreleased), a new `docs/RELEASE_3.2.md`, QUALIFICATION, the TECHNICAL_GUIDE (with the fetch and evaluation recipe), `docs/DATA_LICENSES.md`, and SHA256SUMS. A version bump, tag and release are a separate approval.

## Goals

1. Real-image evidence for BASIRA on all 12 VisA classes, under a protocol fixed in advance.
2. An honest answer to whether the false-alert promise holds on real normals.
3. A fair, interval-backed comparison of the shipped descriptor against DINOv2: as a drop-in inside the engine, and at its native input.
4. Reproducibility by a stranger: a pinned data identity, a manifest, the commands, and the time and hardware stated.

## Non-goals

- Changing the BASIRA engine: its descriptor, profile, limits or decision rule. Promoting DINOv2 to a supported descriptor is a separate decision after the results, likely in phase C.
- Pixel-level localisation metrics (PRO, pixel AUROC), and leaderboard comparisons with published methods.
- Fine-tuning, a GPU, or other datasets (MVTec AD is excluded by licence).
- RASID or SANAD real-data work (later parts of phase B, and phase C).

## Anti-goals (failure even if it runs)

- Any setting chosen, or changed, after seeing a VisA test result. Picking favourable classes, dropping a class, or merging classes to hide a failure.
- A win stated without its interval, or a loss left off the landing or README.
- Implying an industrial certificate, an escape rate, or that VisA is MIZAN's own data.
- Committing the dataset, or using it in a way its licence forbids (missing attribution).
- Network access during evaluation, or evaluating an image whose hash is not in the manifest.

## Constraints

- **Environment.** WSL2 Ubuntu, CPU only. Python 3.12 in `.venv`, with `.venv-hf` for DINOv2.
- **Disk.** The archive is 1.93 GB; 942 GB are free.
- **Time.** Expected runtime is under about an hour. Each DINOv2 pass over about 10,800 images is roughly 15 to 30 minutes on 8 threads. The builder measures and records the actual figures.
- **Determinism.** Fixed seeds everywhere. Thread count fixed, as in 3.1.
- **Invariants and contracts.** CLAUDE.md invariants: frozen signed data, no invented numbers, no MCP authority tools, and engine code changed only for a reproduced failure. The presentation contracts stay intact.
- **Approval.** Downloading the archive needs the owner's explicit approval: it is 1.93 GB, from Amazon's public S3.

## Approaches considered

1. **Each class as a customer product line, with the engine unchanged (chosen).** This tests MIZAN's actual promise (calibrated decisions with a false-alert guarantee) on real data. It reports AUROC as context, compares DINOv2 inside the engine and at native input, and fixes everything in advance. It trades away leaderboard-style metrics for a decision-centred evaluation.
2. **Research-style benchmark** (full-resolution AUROC and PRO against published methods). This was rejected: it departs from MIZAN's decision contract, needs competing methods to be run, and its "who wins" story is not what a company buying a decision system needs.
3. **Add DINOv2 as an official descriptor first, then evaluate.** This was rejected: it changes the engine before the evidence says it is worth it. It is a phase C decision, informed by this result.

The owner picked approach 1 on 25 November 2026, after delegating the how.

## Decisions that need the owner

1. **Amend CLAUDE.md.** Its invariant "All datasets are generated fixtures; never describe them as real" would make every VisA statement look like a violation to later sessions. Proposed replacement: "Bundled datasets are generated fixtures; never describe them as real. The one exception is VisA (`reports/visa/`): real public images under CC BY 4.0, which must be named as such and attributed, and never presented as MIZAN's own data or as an industrial certificate." README's "All inputs are generated fixtures" is corrected in the docs task.
2. **Download the data.** `VisA_20220922.tar` (1,929,840,640 bytes) from Amazon's public S3 bucket `amazon-visual-anomaly` (us-west-2), and `split_csv/1cls.csv` (660,329 bytes) from `github.com/amazon-science/spot-diff`. Both go into `var/visa/`, which git ignores.

## Left to the builder

- **Data and harness code.** The file layout of the fetch and evaluation code, and its function boundaries.
- **Bootstrap and confidence intervals.** The bootstrap implementation, the Clopper–Pearson computation (SciPy's `beta.ppf`) and AUROC ties.
- **Example strip.** The exact rule that picks the examples (it must be stated and fixed before the run), and the page layout within the existing design.
- **Validation and run order.** How the stand-in fixture for CI is generated. How to batch DINOv2 for memory, provided the outputs equal per-image calls.
