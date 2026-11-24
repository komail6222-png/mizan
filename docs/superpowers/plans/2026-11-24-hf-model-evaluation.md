# Hugging Face Model Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the three pinned Hugging Face checkpoints against MIZAN's current components on the existing held-out protocol, behind a fidelity gate, and publish the results.

**Architecture:** A new `mizan/hf_evaluation.py` mirrors the shipped evaluation loops with injectable components (feature function, forecaster, embedder); model loaders in `mizan/models.py` wrap only verified local checkpoints; `scripts/evaluate_hf.py` runs the gates, then the models, and writes `reports/hf/*.json`, which the report page, the site and the docs read.

**Tech Stack:** Python 3.12, NumPy, SciPy (existing); optional `.venv-hf` with CPU torch, transformers, chronos-forecasting, huggingface-hub, sentencepiece (from `requirements-hf.txt`); unittest; WSL Ubuntu.

**Spec:** `docs/superpowers/specs/2026-11-24-hf-model-evaluation-design.md`

## Global Constraints

- Work in WSL at `~/projects/mizan`. Main env: `.venv` (unchanged). Optional env: `.venv-hf` (git-ignored), CPU-only torch from `https://download.pytorch.org/whl/cpu`.
- Do not modify `mizan/evaluation.py`, anything under `reports/evaluation/`, `reports/demo/`, the deployed engines or profiles. New code may import their helpers.
- CI stays torch-free: any test that imports torch, transformers or chronos uses `unittest.skipUnless` on those imports and on `MIZAN_HF_MODELS` (path to the verified cache).
- Determinism: `torch.manual_seed(0)`, `torch.use_deterministic_algorithms(True, warn_only=True)`, `torch.set_num_threads(8)`, `torch.inference_mode()`.
- Protocol values, verbatim from the shipped code: BASIRA frozen items seeds 9100–9179 (index < 40 intact, otherwise `"pit"` if index odd else `"crack"`); pools seeds 30000+ (4000), 40000+ (4000), 50000+ (400, `"pit"` if seed odd else `"crack"`); draws 400; sizes (24, 50, 100); α = 1/20; δ = 1/10; study seed 23. RASID 60 origins starting 2026-01-01 + 42 days, ridge `forecast(..., 4)`. Chronos levels (0.1, 0.3, 0.5, 0.7, 0.9), load path j paired with solar path 4 − j, weights 0.2 each; shared scoring levels (0.1, 0.5, 0.9). SANAD retrieval set 120 documents, seed 2024.
- Gate tolerances: BASIRA exact equality; RASID ridge and seasonal MAE within 1e-9 absolute, `candidate` and `candidate_risk_neutral` totals within 0.05% relative, other baseline totals within 1e-6 relative.
- Every public figure comes from `reports/`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Regenerate `SHA256SUMS` in the last task.

## Review Focus

- A gap in the hourly history before an origin: the Chronos context must use only the contiguous trailing hours before the origin, never data at or after it (test in Task 4).
- A forecaster that returns unsorted or negative quantiles: the harness sorts levels, clips solar at zero, and counts the clipped values (test in Task 4).
- A failing fidelity gate: `scripts/evaluate_hf.py` exits with code 2 and writes no report files (test in Task 6).
- Re-running the fetch when a checkpoint is already imported: verify it with `verify_local` instead of refetching (Task 1 step).
- Memory on 7 GiB: BASIRA scores each pool image as soon as its features are computed and keeps scores, not feature grids (Task 3 implementation note, checked by the full run in Task 6).

---

### Task 1: Optional runtime and verified checkpoints

**Files:**
- Modify: `.gitignore` (add `.venv-hf/`), `requirements-hf.txt` (pin the exact versions installed)

**Interfaces:**
- Produces: `.venv-hf/bin/python` with the optional runtime; verified checkpoints in `var/hf/{sanad,basira,rasid}` with `var/hf/.trust/keys/release.pub`.

- [ ] **Step 1:** Create the env: `uv venv --python 3.12 .venv-hf && uv pip install --python .venv-hf/bin/python -r requirements.txt -e . && uv pip install --python .venv-hf/bin/python --index-url https://download.pytorch.org/whl/cpu torch && uv pip install --python .venv-hf/bin/python -r requirements-hf.txt`
- [ ] **Step 2:** For each kind in sanad, basira, rasid: if `var/hf/<kind>/mizan-manifest.json` exists run `python -c "from mizan.models import verify_local; print(verify_local('<kind>','var/hf'))"`, otherwise `python -m mizan hf fetch <kind>`. Expected: three directories, no `Refused`.
- [ ] **Step 3:** Run `.venv-hf/bin/python -m mizan hf compare basira` (and rasid, sanad). Expected: JSON with `model` and `revision` matching `mizan/models.PINS`.
- [ ] **Step 4:** Replace the ranges in `requirements-hf.txt` with the exact installed versions (`uv pip freeze --python .venv-hf/bin/python`), keeping its header comment updated to "Optional runtime for scripts/evaluate_hf.py".
- [ ] **Step 5:** Commit `.gitignore requirements-hf.txt` — "Pin the optional Hugging Face runtime".

### Task 2: Model loaders over verified checkpoints

**Files:**
- Modify: `mizan/models.py`
- Test: `tests/test_hf_models.py`

**Interfaces:**
- Produces, in `mizan/models.py`:
  - `dinov2_features(root) -> Callable[[PIL.Image.Image], numpy.ndarray]` returning float64 arrays of shape (16, 16, 384), L2-normalised along the last axis.
  - `chronos_forecaster(root) -> Callable[[numpy.ndarray, numpy.ndarray, Sequence[float]], numpy.ndarray]` taking load context, solar context (1-D float arrays, oldest first) and levels; returning shape (24, 2, len(levels)) for (hour, [load, solar], level).
  - `e5_embedder(root) -> Callable[[list[str]], numpy.ndarray]` returning (n, 384) L2-normalised rows; callers add the `query: ` / `passage: ` prefixes.
  - `model_identity(kind, root) -> dict` with `repo`, `revision`, `weight_sha256`, `manifest_sha256`.
  - Each loader calls `verify_local(kind, root)` first and sets `HF_HUB_OFFLINE=1`. `experiment()` is rewritten to use them with unchanged output.

- [ ] **Step 1: Write the tests**

```python
HF = os.environ.get("MIZAN_HF_MODELS")
def _has(mod):
    try: __import__(mod); return True
    except ImportError: return False
@unittest.skipUnless(HF and _has("torch") and _has("transformers") and _has("chronos"), "optional HF runtime")
class LoaderTests(unittest.TestCase):
    def test_dinov2_grid(self):
        f = models.dinov2_features(HF)(examples.part(9100, None))
        self.assertEqual(f.shape, (16, 16, 384)); self.assertTrue(np.allclose(np.linalg.norm(f, axis=-1), 1, atol=1e-5))
    def test_chronos_quantiles(self):
        load = np.linspace(40, 60, 24 * 14); pv = np.tile(np.r_[np.zeros(6), np.linspace(0, 20, 12), np.zeros(6)], 14)
        q = models.chronos_forecaster(HF)(load, pv, [.1, .5, .9])
        self.assertEqual(q.shape, (24, 2, 3)); self.assertTrue(np.all(np.diff(q, axis=-1) >= -1e-9))
    def test_e5_normalised(self):
        v = models.e5_embedder(HF)(["query: Transfer time", "passage: زمن التحويل: ٠ ms"])
        self.assertEqual(v.shape, (2, 384)); self.assertTrue(np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5))
    def test_identity_matches_pin(self):
        self.assertEqual(models.model_identity("basira", HF)["revision"], models.PINS["basira"]["revision"])
```

- [ ] **Step 2:** Run `MIZAN_HF_MODELS=var/hf .venv-hf/bin/python -m unittest tests.test_hf_models -v` — expect failures (functions undefined); `.venv/bin/python -m unittest tests.test_hf_models` — expect skips.
- [ ] **Step 3:** Implement the four functions in `mizan/models.py` (move the DINOv2 preprocessing and E5 pooling out of `experiment`; Chronos via `BaseChronosPipeline.predict_quantiles` once per series with `prediction_length=24`, context truncated to the last 2048 values).
- [ ] **Step 4:** Rerun both commands — expect 4 passed in `.venv-hf`, 4 skipped in `.venv`; `python -m unittest discover -s tests` still OK.
- [ ] **Step 5:** Commit — "Add verified loaders for the pinned Hugging Face models".

### Task 3: BASIRA harness and gate

**Files:**
- Create: `mizan/hf_evaluation.py`
- Test: `tests/test_hf_evaluation.py`

**Interfaces:**
- Produces:
  - `basira_hf(feature_fn, profile: str, *, draws=400, sizes=(24, 50, 100), pools=(4000, 4000, 400)) -> dict` with keys `profile`, `frozen_test` (`test_items`, `detected`, `false_alerts`, `calibration_m`, `bank_digest`, `rows`), `calibration_study` (same keys and row fields as `evaluation.calibration_study()`), `patches_averaged` (the top-2.5% count).
  - `basira_gate(result: dict, shipped: dict) -> dict` with `passed: bool`, `checked: list[str]`, `mismatches: list[str]`; compares `detected`, `false_alerts`, `bank_digest`, `shipped_calibration` and every calibration row field with exact equality against `reports/evaluation/basira.json`.

- [ ] **Step 1: Write the tests**

```python
SHIPPED = json.loads((ROOT / "reports/evaluation/basira.json").read_text())
def test_descriptor_reproduces_shipped_frozen_test(self):
    r = hf.basira_hf(b.features, b.DESCRIPTOR, draws=3, sizes=(24,), pools=(60, 60, 20))
    self.assertEqual((r["frozen_test"]["detected"], r["frozen_test"]["false_alerts"]), (40, 8))
    self.assertEqual(r["frozen_test"]["bank_digest"], SHIPPED["bank_digest"])
def test_gate_reports_each_mismatch(self):
    bad = {"frozen_test": {"detected": 40, "false_alerts": 9, "bank_digest": SHIPPED["bank_digest"]}, "calibration_study": SHIPPED["calibration_study"]}
    g = hf.basira_gate(bad, SHIPPED)
    self.assertFalse(g["passed"]); self.assertIn("false_alerts", g["mismatches"])
```

- [ ] **Step 2:** Run `python -m unittest tests.test_hf_evaluation -v` — expect ImportError/fail.
- [ ] **Step 3:** Implement both functions mirroring `vision_evaluation()` and `calibration_study()` with `feature_fn` passed to `b.fit(..., feature_fn, profile)` and used for every scored image. Score pool images as they are generated; keep only scores.
- [ ] **Step 4:** Rerun — expect PASS. Commit — "Add the BASIRA evaluation harness with a fidelity gate".

### Task 4: RASID harness and gate

**Files:**
- Modify: `mizan/hf_evaluation.py`, `tests/test_hf_evaluation.py`

**Interfaces:**
- Consumes: `evaluation.policy_problem`, `paired_comparison`, `realized`, `tail_mean`; `rasid.forecast.records`, `forecast`, `vintage`, `quantile_scores`; `solve`, `verify`.
- Produces:
  - `Forecaster = Callable[[numpy.ndarray, numpy.ndarray, Sequence[float]], numpy.ndarray]` (the Task 2 shape).
  - `chronos_context(rows, origin) -> tuple[numpy.ndarray, numpy.ndarray, int, datetime]`: load and solar values for the contiguous run of hours ending at the latest hour known at `origin` (`vintage`), oldest first, their count, and that latest hour.
  - `rasid_hf(forecaster, *, days=60) -> dict` with `origins` (per-day MAE, pinball, coverage, and `<policy>_cost` / `<policy>_status` for `POLICIES_HF = evaluation.POLICIES + ("chronos", "chronos_risk_neutral")`), `forecast` (`ridge_mae_kw`, `chronos_mae_kw`, `seasonal_naive_mae_kw`, shared-level pinball and coverage for ridge and Chronos, `pv_values_clipped`), `decision_quality` (same fields as `evaluation.decision_summary`, for all seven policies), `paired_comparisons` (`chronos_risk_neutral` vs `candidate_risk_neutral`, `chronos_risk_neutral` vs `seasonal_naive`, `chronos` vs `candidate`), `scenario_rule` (text).
  - `rasid_gate(result: dict, shipped: dict) -> dict` (same shape as `basira_gate`), tolerances from Global Constraints.

- [ ] **Step 1: Write the tests**

```python
def stub(load, pv, levels, seen=[]):
    seen.append(len(load)); base = np.stack([load[-24:], pv[-24:]], axis=1)
    return base[:, :, None] + np.array(levels)[::-1] - 0.5   # deliberately unsorted along levels
def test_two_days_shapes_and_sorted_quantiles(self):
    r = hf.rasid_hf(stub, days=2)
    self.assertEqual(len(r["origins"]), 2)
    self.assertEqual(r["decision_quality"]["chronos"]["verified_feasible_plans"], 2)
    self.assertGreater(r["forecast"]["pv_values_clipped"], 0)   # the stub's night-time solar goes negative
def test_context_stops_before_origin_and_at_a_gap(self):
    rows = records(examples.electricity(44)); origin = datetime(2026, 2, 12, tzinfo=timezone.utc)
    gap = origin - timedelta(hours=30); rows = [r for r in rows if r[0] != gap]
    load, pv, n, last = hf.chronos_context(rows, origin)
    self.assertLess(last, origin); self.assertGreater(last - timedelta(hours=n), gap - timedelta(hours=1))
    self.assertEqual(len(load), n); self.assertEqual(len(pv), n)
def test_gate_tolerance(self):
    shipped = json.loads((ROOT / "reports/evaluation/rasid.json").read_text())
    ok = hf.rasid_gate(_scaled(shipped, "candidate", 1.0001), shipped); bad = hf.rasid_gate(_scaled(shipped, "candidate", 1.01), shipped)
    self.assertTrue(ok["passed"]); self.assertFalse(bad["passed"])
```
(`_scaled(shipped, policy, factor)` is a test helper that returns a result-shaped dict whose `policy` total is multiplied by `factor`.)

- [ ] **Step 2:** Run the three tests — expect fail.
- [ ] **Step 3:** Implement. Per origin: ridge `forecast(all_rows, origin, grid, 4)` as shipped; `chronos_context`; forecaster quantiles at (0.1, 0.3, 0.5, 0.7, 0.9) then `np.sort` along levels and clip solar at 0 (count clipped); scenarios per the Global Constraints; the two Chronos policies reuse `policy_problem("candidate", ...)` / `policy_problem("candidate_risk_neutral", ...)` with `weights`/`scenarios` replaced; every policy keeps its own state and month peak as `energy_evaluation()` does.
- [ ] **Step 4:** Rerun — expect PASS. Commit — "Add the RASID evaluation harness with a fidelity gate".

### Task 5: SANAD retrieval set and harness

**Files:**
- Modify: `mizan/hf_evaluation.py`, `tests/test_hf_evaluation.py`

**Interfaces:**
- Produces:
  - `retrieval_set(n_docs=120, seed=2024) -> list[dict]`, items `{"doc": {"name", "pages": [str]}, "model": str, "language": "en"|"ar", "gold": {attribute: [line_index, ...]}, "lines": [str]}`; built from `sanad.engine.ATTR` labels with grammar-valid values and the two fixed distractor pools below; asserts gold equals `parse_document` claims and no distractor is a claim.
  - `retrieval_set_digest(items) -> str` (`core.contract.digest` of the documents).
  - `sanad_hf(embed_fn, *, n_docs=120, seed=2024) -> dict` with `set_digest`, `queries` (count), and `metrics[retriever][query_kind][doc_language]` = `{recall_at_1, recall_at_3, mrr_at_4}` for retrievers `lexical` and `e5`, query kinds `bilingual`, `en`, `ar`, languages `en`, `ar`, `all`.

Distractor pools (exact copy):

```python
DISTRACTORS = {"en": ["Input voltage: 230 V", "Weight: 32 kg", "Efficiency (eco mode): 99 %", "Derated 10% above 40 °C",
                      "Rated apparent power varies by configuration", "Dimensions: 440 x 600 mm", "Noise level: 45 dB", "Battery type: VRLA"],
               "ar": ["جهد الدخل: 230 فولت", "الوزن: ٣٢ كجم", "الكفاءة في الوضع الاقتصادي: ٩٩٪", "ينخفض الأداء ١٠٪ فوق ٤٠ درجة",
                      "القدرة الظاهرية الاسمية تختلف حسب التهيئة", "الأبعاد: ٤٤٠ × ٦٠٠ مم", "مستوى الضجيج: ٤٥ ديسيبل", "نوع البطارية: VRLA"]}
```

- [ ] **Step 1: Write the tests**

```python
def test_gold_is_defined_by_the_grammar(self):
    items = hf.retrieval_set(10, seed=7)
    self.assertEqual({i["language"] for i in items}, {"en", "ar"})
    for it in items:
        claims = s.parse_document(it["doc"], it["model"])["claims"]
        self.assertEqual({c["attribute"] for c in claims}, set(it["gold"]))
    self.assertEqual(hf.retrieval_set_digest(items), hf.retrieval_set_digest(hf.retrieval_set(10, seed=7)))
def test_oracle_embedder_scores_one_and_lexical_is_bounded(self):
    r = hf.sanad_hf(_oracle_embedder(), n_docs=10, seed=7)
    self.assertEqual(r["metrics"]["e5"]["bilingual"]["all"]["recall_at_1"], 1.0)
    for v in r["metrics"]["lexical"]["bilingual"]["all"].values(): self.assertTrue(0 <= v <= 1)
```
(`_oracle_embedder()` maps a `query:` string and the gold `passage:` lines for the same attribute to the same unit vector, using `ATTR` labels and the gold line text.)

- [ ] **Step 2:** Run — expect fail.
- [ ] **Step 3:** Implement. Lexical ranking = `s.retrieve([{"quote": line, "page": 1, "source": ""} ...], query)` (top four); E5 ranking = cosine over all lines with `query: ` / `passage: ` prefixes; bilingual query = `" ".join(s.ATTR[a])`.
- [ ] **Step 4:** Rerun — expect PASS; full suite OK. Commit — "Add the SANAD retrieval set and harness".

### Task 6: The evaluation run

**Files:**
- Create: `scripts/evaluate_hf.py`
- Modify: `tests/test_hf_evaluation.py`
- Create (by running): `reports/hf/basira.json`, `reports/hf/rasid.json`, `reports/hf/sanad.json`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: `run(models_root, out, only=None, repeat=False, *, loaders=None, quick=False) -> dict` (`quick` uses draws 3, sizes (24,), pools (60, 60, 20), 2 days and 10 documents, for tests only; quick results are never written) and a CLI `python scripts/evaluate_hf.py --models var/hf --out reports/hf [--only basira|rasid|sanad] [--repeat]`. Each JSON holds `gate`, `baseline` (current component result), `model` (model result), `identity` (`model_identity`), `environment` (`evaluation.environment()` plus torch, transformers, chronos-forecasting versions and torch threads), `settings` (the fixed values from Global Constraints), `repeat_run` (`{"identical": bool}` when `--repeat`), `elapsed_seconds`. Files are written only after every requested gate passes, via a temporary file and rename. A failed gate exits with code 2.

- [ ] **Step 1: Write the test**

```python
def test_failed_gate_writes_nothing(self):
    with tempfile.TemporaryDirectory() as d, mock.patch.object(hf, "basira_gate", return_value={"passed": False, "checked": [], "mismatches": ["detected"]}):
        with self.assertRaises(SystemExit) as e:
            evaluate_hf.run("unused", d, only="basira", loaders={"basira": lambda root: b.features}, quick=True)
        self.assertEqual(e.exception.code, 2); self.assertEqual(list(Path(d).iterdir()), [])
```

- [ ] **Step 2:** Run — expect fail. Implement `scripts/evaluate_hf.py`. Rerun — expect PASS.
- [ ] **Step 3:** Full run: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv-hf/bin/python scripts/evaluate_hf.py --models var/hf --out reports/hf --repeat` (run in the background; expect tens of minutes). Expected: three files, every `gate.passed` true, `repeat_run.identical` true (if false, the report says so; do not hide it).
- [ ] **Step 4:** Read the three reports and write down, before any presentation work, the plain result per system (better, worse, or unresolved, with the numbers).
- [ ] **Step 5:** Commit the script, test and `reports/hf/` — "Evaluate the pinned Hugging Face models on the held-out protocol".

### Task 7: Report page section

**Files:**
- Modify: `scripts/build_report.py`, `tests/test_evaluation.py`, `reports/evaluation/index.html` (regenerated)

**Interfaces:**
- Consumes: `reports/hf/*.json` (Task 6).
- Produces: `summarize(source, hf_source=ROOT / "reports/hf")` adds a `hf` key when the directory exists; the page gains a "Foundation models from Hugging Face" section with one table per system (current component next to the model, the numbers from Task 6's reports, and the RASID paired interval).

- [ ] **Step 1: Write the test** — in `ReportPageTests`, build into a temp dir and assert `"Foundation models from Hugging Face" in page`, the embedded data has `hf.basira.model.frozen_test.detected`, and `len(page) < 60000` still holds.
- [ ] **Step 2:** Run — expect fail. Implement. Rerun — expect PASS.
- [ ] **Step 3:** Regenerate `python scripts/build_report.py`; `python scripts/check_exhibits.py --chromium PATH --site _site --out /tmp/b.json` — expect PASS (report charts still answer hover).
- [ ] **Step 4:** Commit — "Show the Hugging Face results in the evaluation report".

### Task 8: Site, documentation and release record

**Files:**
- Modify: `scripts/build_site.py`, `site/landing.html`, `README.md`, `README.ar.md`, `CHANGELOG.md`, `CLAUDE.md`, `docs/QUALIFICATION.md`, `reports/application-tests.txt`, `SHA256SUMS`
- Create: `docs/RELEASE_3.1.md`

**Interfaces:**
- Consumes: `reports/hf/*.json`.
- Produces: `facts()` keys `hf_basira_detected`, `hf_basira_false`, `hf_rasid_mae`, `hf_rasid_value`, `hf_rasid_phrase_en`, `hf_rasid_phrase_ar`, `hf_sanad_lexical_r1`, `hf_sanad_e5_r1` (the phrase keys say "resolved" or "not resolved" from the interval sign of `chronos_risk_neutral` vs `candidate_risk_neutral`); a landing evidence block "Foundation models, measured honestly" in English and Arabic using only those keys.

- [ ] **Step 1:** Add the facts and the landing block; `python scripts/build_site.py` prints the new keys; `python scripts/check_exhibits.py --chromium PATH --site _site --out /tmp/b.json` PASS; contrast audit shows no failures.
- [ ] **Step 2:** Write `docs/RELEASE_3.1.md` (the redesign and the Hugging Face evaluation: protocol, gate, results with numbers from `reports/hf/`, limits), the CHANGELOG entry, README and README.ar rows, the QUALIFICATION M02 row, and the CLAUDE.md command `python scripts/evaluate_hf.py --models var/hf --out reports/hf --repeat` (`--out` is required, and `reports/hf` is refused without `--repeat`).
- [ ] **Step 3:** Run the full suite in `.venv` (`python -m unittest discover -s tests -v`), write the result into `reports/application-tests.txt` with its header, then regenerate `SHA256SUMS` and run `sha256sum -c SHA256SUMS` — expect all OK.
- [ ] **Step 4:** Commit — "Publish the Hugging Face evaluation on the site and in the docs".
