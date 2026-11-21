# MIZAN implementation package

The original product scope is in `BUILD_BRIEF_v1.pdf`. The engineering amendments are in `MIZAN_Architecture_v2.0.md`. Read the two together. Where a rule or acceptance test conflicts, v2.0 controls. The reference script runs on Python's standard library and uses temporary local databases.

## Copy into Codex

```text
Build MIZAN from the files in this workspace. Locate BUILD_BRIEF_v1.pdf and MIZAN_Architecture_v2.0.md first and read both. The PDF defines the three products and their workflows. The amendment supersedes conflicting technical requirements and tests. Use its 16 findings, 10 invariants and 28 acceptance cases as the implementation contract.

Inspect the repository, Python runtime, actual SQLite version, disk and available compute. Record a short requirements-to-code/test map. Preserve existing user work. Implement Profile S first and begin with the first running path in section 12.2. Continue through the three product slices and the applicable release gates. Make changes, run each slice and record the evidence. When distributed infrastructure or representative data is unavailable, keep its qualification explicitly open and continue the local work that can run.

SANAD must return an evidence-backed bilingual UPS verdict and handle an authority-policy change. BASIRA must show the operating mode its actual calibration count and independent defect evidence support. RASID must publish expected cost, tail cost, physical residuals and the independently checked full objective. Each system needs a working local demo, public-safe fixtures, pinned Hugging Face artifacts and a reproducible evaluation against a baseline.

Run verification/reference_checks.py as a small mathematical/SQLite reference. Implement the acceptance tests against the real application separately. For each milestone, report the runnable command, an actual output or report path, and remaining evidence gaps. Record deployment cost or model-download requirements before using paid resources. Prepare Hugging Face demo artifacts that can be run and reviewed locally.
```

## Reproduce the companion checks

From the extracted directory, run the following command with Python 3 and assertions enabled.

```bash
python3 verification/reference_checks.py my_results.json
```

`verification/verification_results.json` records the executed eight-group reference run. Two independent executions may report different interpreter/library versions. The SQLite process-exit fixture creates and closes temporary databases and leaves the repository data untouched. Compare the assertions and evidence values. The extended production acceptance matrix is in section 12.1 of the amendment.

`SHA256SUMS` records the byte identities of the packaged inputs, prompt and verification files. It supports local integrity checking. Its entries do not constitute publisher signatures.

