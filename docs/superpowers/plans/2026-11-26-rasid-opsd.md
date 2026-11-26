# RASID on Real Load (OPSD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate RASID's unchanged forecaster, planner and verifier on two real business sites from the Open Power System Data household package, under a protocol sealed before any site day is forecast. Measure planning quality without currency. Publish the evidence, a results page, a guarded landing block and the docs.

**Architecture:**
- `mizan/opsd.py` owns the data and the sealed protocol: the file's identity, the hourly energy rule, clean days, the no-export gate, target days and runs, site sizing, and `PROTOCOL` with `SEALED_DIGEST`. It is torch-free and pandas-free.
- `mizan/opsd_evaluation.py` owns the day loop, the within-run statistics, the gates and the record. The Chronos forecaster is injected, so tests stay torch-free.
- `scripts/fetch_opsd.py` downloads and checks the file. `scripts/evaluate_opsd.py` runs the gates and the evaluation, and writes `reports/opsd/` under the VisA runner's discipline.
- `scripts/build_opsd_report.py` renders one page. `build_site.py` serves it and adds guarded landing facts.

**Tech Stack:** Python 3.12, NumPy, SciPy (HiGHS MILP, via the shipped `rasid.solver`), stdlib `csv` and `zoneinfo`. `.venv-hf` (torch 2.15.0+cpu, chronos-forecasting 2.3.2) for Chronos and the real run. unittest, Playwright Chromium, WSL Ubuntu.

**Spec:** `docs/superpowers/specs/2026-11-26-rasid-opsd-design.md`, rules 1–12. The plan cites those rule numbers.

## Global Constraints

### Environment, frozen files and CI

- Work in WSL at `~/projects/mizan` on branch `rasid-opsd`. Use `.venv` for everything torch-free, and `.venv-hf` only for Chronos and the real run.
- **Do not modify:**
  - `mizan/rasid/*`, `mizan/evaluation.py`, `mizan/hf_evaluation.py`, `mizan/models.py`, `mizan/examples.py`, `mizan/visa*.py`;
  - `reports/evaluation/`, `reports/hf/`, `reports/demo/`, `reports/visa/`;
  - the signed pages.

  Import their functions instead.
- **CI stays torch-free and network-free:**
  - no test downloads anything or reads `var/opsd/`;
  - tests use the stand-in CSV from Task 1;
  - Chronos is replaced by a stub forecaster in tests.

### Data source (verbatim, spec "Data")

- **File:** `https://data.open-power-system-data.org/household_data/2020-04-15/household_data_60min_singleindex.csv`, 15,654,559 bytes, SHA-256 `15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60`, version 2020-04-15.
- **Licence:** CC BY 4.0. Primary data from the CoSSMic project, Konstanz. The owner approved this download, and only this one.
- **Attribution text** (`mizan.opsd.ATTRIBUTION`, verbatim): `Open Power System Data. 2020. Data Package Household Data. Version 2020-04-15. https://data.open-power-system-data.org/household_data/2020-04-15/. Primary data from the CoSSMic project (Konstanz, Germany). CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. The load of each site is reconstructed as grid import plus solar; values were differenced from cumulative meter readings.`

### Sites

| Key | Building | Import column | Solar columns |
|---|---|---|---|
| `industrial1` | warehouse | `DE_KN_industrial1_grid_import` | `DE_KN_industrial1_pv_1`, `DE_KN_industrial1_pv_2` |
| `industrial3` | research-institute building | `DE_KN_industrial3_grid_import` | `DE_KN_industrial3_pv_facade`, `DE_KN_industrial3_pv_roof` |

### Protocol values (verbatim, spec rules)

- **Time:** zone `Europe/Berlin`.
- **Hourly energy (rule 1):** energy of hour t = reading(t) − reading(t − 1h). It is missing when either row is absent, the difference is below −1e-9, or a used column is named in `interpolated` at t or t − 1h.
- **Clean days (rule 3):**
  - every hour present;
  - grid import not identical, hour by hour, to the previous local day;
  - no run of ≥ 4 identical non-zero hourly imports;
  - comparisons after rounding to 1e-6.
- **No-export gate (rule 4):** solar > 0.2 × p99(solar) and import < 0.02 × median(load) over clean hours, where any such hour fails.
- **Target days (rule 5):** a 24-hour clean day whose 7 previous local days are clean, and which the ridge forecaster accepts.
- **History (rule 6):** clean hours before the origin within 300 days, with `known_at` = time + 1 h. Chronos context: at most 2,048 contiguous clean hours ending at origin − 1 h.
- **Sizing (rule 7):**
  - s = (mean load over the first 28 clean days) ÷ the generated mean load of `examples.electricity(102)`, which is 67.214 kW, computed;
  - capacity 80·s, power 20·s, initial and terminal 40·s;
  - grid limit 2 × the maximum clean hourly load;
  - all prices, risk 0.35, β 0.90, efficiencies and cycles unchanged.
- **State (rule 8):** the peak resets at each local month's start. Both the peak and the charge reset at each run start: the initial charge (no_battery at 0) and a peak of 0.
- **Statistics (rules 9 and 10):**
  - block of 7 days within a run, with a shorter run as one block;
  - 4,000 draws, seed 2026;
  - pooled with equal site weight;
  - normalisers fixed.
- **Refusals (rule 12):** counted. The day is dropped for every policy, and the run ends.
- **Policies:** `hf_evaluation.POLICIES_HF`. Chronos levels and scenarios: `hf_evaluation.CHRONOS_LEVELS`, `SHARED_LEVELS`, `SCENARIO_RULE`.

### Copy rules

- No currency anywhere on the page, the landing block or the docs' OPSD sections. The words `euro`, `EUR`, `€`, `saving` and `money` are forbidden there. Costs are "illustrative tariff units".
- Load is always "reconstructed (grid import + solar)", never "measured load".
- The attribution appears on the page and in `docs/DATA_LICENSES.md`.
- Readings and directions are computed from interval signs. An interval that touches zero reads as not resolved.

## Review Focus

- **A daylight-saving day inside a look-back.** A 23- or 25-hour clean day is excluded as a target, but it still counts as clean for the next day's 7-day look-back. Pinned in Task 1 by `test_dst_day_is_clean_but_not_a_target`.
- **A meter step backwards or a missing row in the middle of a day.** That hour is missing, so the day is not clean, and neither that day nor the next 7 are targets. Pinned in Task 1 by `test_negative_step_and_missing_row_unclean_the_day`.
- **Refusals at a run's first day, or two in a row.** Every policy loses the same day, the next day starts a new run with reset state, and the paired rows stay aligned. Pinned in Task 4 by `test_refusal_drops_the_day_for_all_and_restarts_the_run`.
- **A downloaded file of the right size but a different hash.** It is refused (`OPSD_HASH`) before any row is parsed. Pinned in Task 1 by `test_fetch_refuses_a_changed_file`, and in Task 6 by the runner's verification.
- **Short runs and empty sites.** A run shorter than 7 days is one block. A site with no target day stops the run (`OPSD_NO_DAYS`) rather than reporting empty statistics. Pinned in Task 3 by `test_short_runs_are_whole_blocks` and `test_a_site_without_target_days_is_refused`.

---

### Task 1: Data rules and fetch

**Files:**
- Create: `mizan/opsd.py` (data part), `scripts/fetch_opsd.py`, `tests/opsd_fixture.py`, `tests/test_opsd.py`

**Interfaces:**
- Produces, in `mizan/opsd.py`:
  - `URL`, `FILE_NAME`, `FILE_BYTES = 15654559`, `FILE_SHA256`, `VERSION = "2020-04-15"`, `ZONE = "Europe/Berlin"`, `ATTRIBUTION` (verbatim from Global Constraints), `SITES: dict[str, tuple[str, tuple[str, str]]]` and `BUILDINGS: dict[str, str]`.
  - `hourly(path: Path, site: str) -> dict[datetime, tuple[float, float]]`: UTC hour start → (grid import kWh, solar kWh) for every usable hour (rule 1), read with `csv.DictReader`.
  - `clean_days(hours, zone=ZONE) -> dict[date, list[datetime]]`: local date → its UTC hours, clean days only (rules 2 and 3). Load = import + solar.
  - `export_gate(hours, clean) -> dict`: `{"sunny_hours": int, "low_import_hours": int, "passed": bool, "solar_threshold_kw": float, "import_threshold_kw": float}` (rule 4).
  - `target_candidates(clean) -> list[date]`: 24-hour clean days whose 7 previous local days are clean (rule 5, before the ridge check), sorted.
  - `runs(days: list[date]) -> list[list[date]]`: maximal runs of consecutive dates.
  - `generated_mean_load() -> float`: the mean of `examples.electricity(102)`'s load, 67.214… kW.
  - `site_base(hours, clean) -> dict`: `{"scale": s, "limit_kw": float, "settings": examples.settings(24) with assets scaled and limit set}` (rule 7).
  - `history_rows(hours, clean, origin: datetime, days=300) -> list[dict]`: rows in the `rasid.forecast.records` format (`time`, `known_at`, `load`, `pv`).
- Produces, in `tests/opsd_fixture.py`: `make_opsd_csv(path: Path, *, days=40, start="2016-03-01") -> Path`. It writes a CSV with the real column names for both sites, plus `utc_timestamp` and `interpolated`, holding cumulative readings of a smooth load (40–90 kW) and solar (0–10 kW). It includes a daylight-saving changeover (2016-03-27), one flagged hour, one copied day, one straight-line day, one negative step and one missing row, each on a known date the tests name.
- Produces `scripts/fetch_opsd.py`, with CLI `--out var/opsd [--url URL]`. It downloads with `scripts.fetch_visa.download`, refuses a size or SHA-256 other than the constants (`OPSD_HASH`) and keeps no partial file. If the file is already present and matches, it reports so.

- [ ] **Step 1: Write the failing tests** in `tests/test_opsd.py`:
  - `test_hourly_energy_is_the_reading_minus_the_previous_one`: on the fixture, hour t's import equals the cumulative value at t minus the value at t − 1h. The first row has no energy.
  - `test_flagged_hours_are_missing`: an hour whose own row or previous row names a used column in `interpolated` is absent from `hourly`.
  - `test_negative_step_and_missing_row_unclean_the_day`: the named dates are not in `clean_days`, and neither they nor the next 7 days are in `target_candidates`.
  - `test_copied_and_straight_line_days_are_unclean`: the copied date and the straight-line date are excluded.
  - `test_dst_day_is_clean_but_not_a_target`: 2016-03-27 has 23 hours and is in `clean_days`, but not in `target_candidates`. 2016-03-28 is a candidate when its other 6 previous days are clean.
  - `test_export_gate_fails_on_an_export_hour`: zeroing one sunny hour's import in a copy of the fixture makes `export_gate(...)["passed"]` false, with `low_import_hours == 1`.
  - `test_site_base_scales_the_generated_settings`: with all load = 67.214 kW, `scale` ≈ 1 and the assets equal `examples.settings(24)`'s. With load doubled, they double and prices are unchanged. `limit_kw == 2 * max load`.
  - `test_history_rows_respect_the_window_and_vintage`: there are no rows at or after the origin and none older than 300 days, and `known_at` = time + 1 h.
  - `test_fetch_refuses_a_changed_file`: a `file://` URL to a file of the right size (via monkeypatched constants) but different content exits non-zero, with no file left.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_opsd -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement to the interfaces. Parse `utc_timestamp` with format `%Y-%m-%dT%H%M%SZ`. Compare timestamps for exact one-hour spacing. Group by local date via `zoneinfo`.
- [ ] **Step 4:** Run the tests (PASS), then the full suite: `.venv/bin/python -m unittest discover -s tests` (OK).
- [ ] **Step 5:** Run the real fetch: `.venv/bin/python scripts/fetch_opsd.py --out var/opsd`. Expected: the existing file verifies (size and SHA-256). Then print each site's clean-day count, target candidates, runs and export gate, read only through `mizan.opsd`. Expected: close to the design's inspection figures (601 and 443 clean days, 547 and 276 candidates). Record any difference in the task report; the evidence will carry the sealed counts.
- [ ] **Step 6:** Commit `mizan/opsd.py scripts/fetch_opsd.py tests/opsd_fixture.py tests/test_opsd.py` with the message "Read OPSD business sites under explicit data rules".

### Task 2: Sealed protocol

**Files:**
- Modify: `mizan/opsd.py` (protocol part), `tests/test_opsd.py`

**Interfaces:**
- Produces:
  - `PROTOCOL: dict`. Keys: `data` (URL, bytes, SHA-256, version, sites, buildings), `rules` (each of rules 1–12 as a short string with its values), `sizing`, `forecasters`, `policies` (`POLICIES_HF`), `statistics`, `refusals`, `gate`.
  - `SEALED_DIGEST: str = digest(PROTOCOL)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_protocol_digest_is_sealed`: `digest(PROTOCOL) == SEALED_DIGEST`.
  - `test_protocol_carries_the_spec_values`: the 7-day look-back, 300 days, 2,048 hours, the 0.2 and 0.02 gate fractions, 28 sizing days, the 2× limit, 4,000 draws, seed 2026, block 7 and the file SHA-256 all appear with those values.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Write `PROTOCOL` and set `SEALED_DIGEST` to its digest. Make the data functions of Task 1 read their constants from `PROTOCOL`, so a protocol change changes behaviour and the digest together.
- [ ] **Step 4:** Run `tests.test_opsd` (PASS), then the full suite (OK).
- [ ] **Step 5:** Commit `mizan/opsd.py tests/test_opsd.py` with the message "Seal the OPSD protocol". **This commit is the seal.** No site day may be forecast or planned before it, and its SHA is recorded in the ledger.

### Task 3: Within-run statistics

**Files:**
- Create: `mizan/opsd_evaluation.py` (statistics part), `tests/test_opsd_evaluation.py`

**Interfaces:**
- Produces:
  - `run_blocks(run_lengths: list[int], block=7) -> list[tuple[int, int]]`: (start, length) of every allowed block in the concatenated day order. A run shorter than `block` contributes one block of its full length.
  - `block_draws(n: int, run_lengths, *, draws=4000, seed=2026) -> np.ndarray`: shape (draws, n), the resampled day indices, built by concatenating uniformly chosen blocks and truncating to n.
  - `paired(diffs: np.ndarray, run_lengths, **kw) -> dict`: `{"mean": float, "total": float, "ci95": [lo, hi], "reading": "higher" | "lower" | "not resolved", "bootstrap": str}`. Readings use `visa_evaluation.reading`.
  - `share_of_value(rows, policy) -> float`: (Σ no_battery − Σ policy) ÷ (Σ no_battery − Σ perfect_foresight), None when the denominator ≤ 0.
  - `share_interval(rows, policy, run_lengths, **kw) -> dict`: `{"value", "ci95"}`, using the same draws.
  - `pooled(per_site: dict[str, tuple[np.ndarray, list[int]]], **kw) -> dict`: the equal-weight mean of the sites' unit-free means. Each draw resamples each site independently (site seed = seed + index in sorted site order), and the result carries `ci95` and `reading`.

- [ ] **Step 1: Write the failing tests:**
  - `test_blocks_never_span_a_gap`: with run lengths [10, 3, 8], every block lies within one run, the 3-day run yields exactly one block (start 10, length 3), and the 10-day run yields starts 0–3.
  - `test_short_runs_are_whole_blocks`: with run lengths [2, 2], the draws use only blocks of length 2.
  - `test_paired_reading_follows_the_interval`: constant positive differences read "higher", constant negative ones read "lower", and alternating ±1 reads "not resolved". An interval whose upper end is exactly 0 reads "not resolved".
  - `test_share_of_value_matches_hand_arithmetic`: no_battery = [10, 10], perfect = [6, 6] and policy = [7, 8] give 0.625.
  - `test_pooled_weights_sites_equally`: two sites with constant unit-free differences 0.1 (20 days) and 0.3 (200 days) pool to 0.2.
  - `test_draws_are_reproducible`: the same seed gives identical arrays.
  - `test_a_site_without_target_days_is_refused`: `paired(np.array([]), [])` raises `Refused` with code `OPSD_NO_DAYS`.
- [ ] **Step 2:** Run `.venv/bin/python -m unittest tests.test_opsd_evaluation -v`. Expected: FAIL.
- [ ] **Step 3:** Implement to the interfaces, with `numpy.random.default_rng(seed)`.
- [ ] **Step 4:** Run (PASS), then the full suite (OK).
- [ ] **Step 5:** Commit with the message "Add within-run block statistics for OPSD".

### Task 4: The day loop and the gates

**Files:**
- Modify: `mizan/opsd_evaluation.py` (harness part), `tests/test_opsd_evaluation.py`

**Interfaces:**
- Consumes:
  - from `hf_evaluation`: `POLICIES_HF`, `PLANNED_AS`, `CHRONOS_LEVELS`, `SHARED_LEVELS`, `SCENARIO_RULE`, `chronos_context`, `_columns`, `_decision_summary`, `rasid_gate`;
  - from `evaluation`: `realized`, `POLICIES`;
  - from `rasid`: `forecast`, `records`, `quantile_scores`, `checked`, `solve`, `verify`.
- Produces:
  - `day_problem(name, f, y, state, peak, base: dict) -> dict`. It mirrors `evaluation.policy_problem`, with `base` (a `site_base(...)["settings"]` dict, or `examples.settings(24)` for the generated site) in place of `examples.settings(24)` and the terminal charge taken from `base`.
  - `plan_days(origins: list[list[datetime]], rows_at: Callable[[datetime], list], actual: Callable[[datetime], tuple], base: dict, forecaster, *, grid_at: Callable[[datetime], list[datetime]], month_of: Callable[[datetime], tuple]) -> dict`. It returns `{"rows": [...], "run_lengths": [...], "ridge_refused": {cause: n}, "planning_refused": [{"date", "policy", "cause"}]}`. Each row carries the fields `rasid_hf` produces per origin, plus `run`.
  - `generated_gate(forecaster) -> dict`: `{"passed", "ridge": rasid_gate(...), "chronos": {...}}`. It runs `plan_days` on `examples.electricity(102)` with the shipped settings and the 60 UTC origins of `rasid_hf` as one run, then checks the ridge side against `reports/evaluation/rasid.json` and the Chronos side against `reports/hf/rasid.json`. The Chronos side checks `chronos_mae_kw`, `chronos_mean_pinball`, `chronos_truncated_crps` and `chronos_coverage_80_load` within 1e-9, the chronos and chronos_risk_neutral totals within 1e-6 relative, and plan counts exactly.

- [ ] **Step 1: Write the failing tests** (stub forecaster: the seasonal path ± 5% as quantiles):
  - `test_day_problem_equals_the_shipped_problem_at_scale_one`: for every name in `evaluation.POLICIES`, `day_problem(..., base=examples.settings(24) with limit 150)` == `evaluation.policy_problem(...)` on one generated origin.
  - `test_plan_days_reproduces_rasid_hf_with_the_same_forecaster`: with the stub, `plan_days` on the 60 generated origins gives the same per-origin costs, statuses and errors as `hf_evaluation.rasid_hf(stub)`.
  - `test_refusal_drops_the_day_for_all_and_restarts_the_run`: `rasid.solver.solve` is patched to raise `Refused("NO_INCUMBENT")` for one policy on day 3 of a 6-day run. Then:
    - rows exist for days 1–2 and 4–6 for every policy;
    - `planning_refused` names the day, the policy and the cause;
    - day 4 starts run 2 with the initial charge and a peak of 0.

    The same happens with two refusals in a row, and with a refusal on a run's first day.
  - `test_ridge_refusal_is_counted_not_fatal`: a day whose history is too short is counted under its cause (`WARMUP` or `CALIBRATION`) and ends the run.
  - `test_month_start_resets_only_the_peak`: across a month boundary inside a run, the charge carries and the peak resets.
  - `test_generated_gate_fails_closed`: with `reports/evaluation/rasid.json` patched by +1% on one total, `generated_gate(...)["passed"]` is false and names the field.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `plan_days` operation by operation like `rasid_hf`, with these differences:
  - the origins come grouped in runs;
  - state resets at each run start;
  - the month uses `month_of`;
  - refusals follow rule 12;
  - history comes from `rows_at`.

  Do not change `hf_evaluation`.
- [ ] **Step 4:** Run (PASS), then the full suite (OK). Then run `generated_gate` once with the real Chronos in `.venv-hf` (`MIZAN_HF_MODELS=var/hf`). Expected: passed, with both sides at 0 mismatches. Record its time.
- [ ] **Step 5:** Commit with the message "Add the OPSD day loop and the generated-site gate".

### Task 5: The evidence record

**Files:**
- Modify: `mizan/opsd_evaluation.py` (record part), `tests/test_opsd_evaluation.py`

**Interfaces:**
- Produces:
  - `COMPARISONS` (the six cost comparisons of rule 9, as `(a, b)` tuples) and `ACCURACY` (`("ridge_mae", "naive_mae")`, `("chronos_mae", "ridge_mae")`).
  - `QUICK = {"days_per_site": 10, "draws": 50}`.
  - `evaluate_opsd(path: Path, forecaster, *, quick=False) -> dict`. It returns:
    - `identity`: the file SHA-256, URL and version;
    - `settings`: `PROTOCOL`;
    - `quick`, `notes` (including `ATTRIBUTION` and the reconstruction note);
    - `sites`: for each site, `building`, `base` (scale, battery, limit), `counts` (clean, 24-hour, look-back excluded, daylight-saving excluded, ridge refused by cause, planning refused, target days, runs), `export_gate`, `forecast` (the means as in `rasid_hf`, plus MAE ÷ mean load), `decision_quality` (`_decision_summary`), `share` (per policy, with `ci95`), `paired` (`COMPARISONS`), `accuracy` (`ACCURACY`) and `rows`;
    - `pooled`: the share per policy, the comparisons normalised by the site's mean daily no-battery cost, and the accuracy differences normalised by the site's mean load, each with `ci95` and `reading`, plus the normalisers;
    - `scenario_rule`.
  - It refuses (`OPSD_EXPORT`) when a site's export gate fails, before anything is forecast.

- [ ] **Step 1: Write the failing tests** on the fixture CSV, with the stub forecaster and `quick=True`:
  - `test_record_shape`: both sites are present with every key above, `counts.target_days == len(rows)` and `sum(run_lengths) == len(rows)`.
  - `test_export_gate_stops_before_forecasting`: with a fixture export hour, `evaluate_opsd` raises `OPSD_EXPORT` and the stub forecaster was never called.
  - `test_pooled_uses_fixed_normalisers`: the pooled normalisers equal each site's mean daily no-battery cost and mean load over all its target days.
  - `test_no_currency_in_notes`: the notes contain none of the forbidden words.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Origins are local midnights of the target candidates, converted to UTC. The grid is the 24 UTC hours of that local day. `month_of` is the local (year, month).
- [ ] **Step 4:** Run (PASS), then the full suite (OK).
- [ ] **Step 5:** Commit with the message "Assemble the OPSD evidence record".

### Task 6: Runner and the real run

**Files:**
- Create: `scripts/evaluate_opsd.py`, `reports/opsd/rasid.json`, `reports/opsd/runs.json`
- Test: `tests/test_opsd_evaluation.py` (`RunnerTests`)

**Interfaces:**
- Consumes: from `scripts.evaluate_visa`, `_git`, `_git_state`, `_append_log`, `_peak_rss_mib`; from `scripts.evaluate_hf`, `_write`, `differences`, `environment`.
- Produces:
  - `EVIDENCE_DIR = reports/opsd`, `RUN_LOG`, `CHILD_ENV = "MIZAN_OPSD_REPEAT_OUT"`, `VOLATILE = ("elapsed_seconds", "environment", "repeat_run", "provenance")`.
  - `check_seal(seal)`: the VisA rule applied to `mizan/opsd.py` and `opsd.SEALED_DIGEST`. The seal commit must carry the digest, and its parent must not.
  - `run(data, out, *, models_root, reason, repeat=False, seal=None, forecaster=None, quick=False, log=RUN_LOG, child=False)`. Order of operations:
    1. the child guard;
    2. the evidence-directory guard;
    3. the seal and protocol digest;
    4. file size and SHA-256 (`OPSD_HASH`);
    5. the generated gate (exit 2 on failure);
    6. the "started" log entry;
    7. `evaluate_opsd`;
    8. the repeat in a second process (or in the same process with an injected forecaster, said so in `repeat_run.process`);
    9. environment and provenance;
    10. write;
    11. the "finished" log entry.
  - CLI: `--data --models --out --reason --seal --repeat`, plus hidden `--quick` and `--child`.

- [ ] **Step 1: Write the failing tests**, mirroring the VisA `RunnerTests`:
  - the evidence directory needs `--repeat`;
  - a real run needs the seal and a matching digest;
  - a changed file is refused before parsing (`OPSD_HASH`);
  - a failed gate writes nothing and exits 2;
  - the log has "started" before scoring and "finished" after, and a crash leaves the "started" entry;
  - child mode only runs for its parent's folder;
  - the repeat names its folder to the child;
  - the seal is the commit that introduces the digest;
  - a stub run writes a gated, repeated record.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement to the interfaces.
- [ ] **Step 4:** Run (PASS), then the full suite (OK). Commit `scripts/evaluate_opsd.py tests/test_opsd_evaluation.py` with the message "Add the OPSD runner". Note the commit in the ledger.
- [ ] **Step 5: The real run.** It starts from a clean tracked tree, in the background:

  `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MIZAN_HF_MODELS=var/hf .venv-hf/bin/python scripts/evaluate_opsd.py --data var/opsd --models var/hf --out reports/opsd --repeat --seal <Task 2 commit> --reason "sealed protocol, first real run"`

  Expected: the gate passed, the repeat identical, `rasid.json` written. Report the headline figures exactly as stored.
- [ ] **Step 6:** Commit `reports/opsd/rasid.json reports/opsd/runs.json` with the message "Evaluate RASID on OPSD business sites".

### Task 7: Results page

**Files:**
- Create: `scripts/build_opsd_report.py`, `reports/opsd/index.html`
- Modify: `scripts/build_report.py` (an `OPSD_LINK` section, id `opsd-link`, href `opsd/index.html`, included when the page exists), `scripts/build_site.py` (`COPIED`: `report/opsd/index.html`), `scripts/check_exhibits.py` (`check_opsd`)
- Test: `tests/test_evaluation.py` (`OpsdPageTests`)

**Interfaces:**
- Produces `build(source=reports/opsd, out_dir=reports/opsd) -> list[Path]`.
  - It refuses a record whose gate failed, whose repeat is missing or not identical, or that is a quick run.
  - It reuses `build_report._page` and the VisA page helpers' style. The protocol's numbers come from the record's `settings`.
  - Element ids: `opsd-section`, `opsd-lede`, `opsd-sites`, `opsd-days`, `opsd-accuracy`, `opsd-decision`, `opsd-share`, `opsd-paired`, `opsd-pooled`, `opsd-attribution`.

- [ ] **Step 1: Write the failing tests** on a stand-in record built with `evaluate_opsd(fixture, stub, quick=True)`, marked non-quick with gate and repeat fields set as in `_visa_record`:
  - the page is under 60,000 characters, has no Arabic codepoints and makes no external load;
  - the embedded counts equal the source's per-site target days and planned-day counts;
  - the attribution appears verbatim;
  - the page contains "reconstructed" and "illustrative tariff units", and none of the forbidden currency words;
  - the build refuses a failed gate, a missing or non-identical repeat, and a quick record;
  - no protocol number is typed into the page source (`7-day`, `4000`, `0.35` are absent; they come from `cfg`).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Add `check_opsd` to `check_exhibits.py`. It checks:
  - every paired and pooled Reading cell equals the stored reading;
  - no overflow at 390 px;
  - `.fit` tables fit;
  - zero external requests;
  - contrast in light and dark.

  Call it when `reports/opsd/index.html` exists. The report check asserts `#opsd-link`.
- [ ] **Step 4:**
  1. Run `.venv/bin/python scripts/build_opsd_report.py`, `scripts/build_report.py` and `scripts/build_site.py`.
  2. Run `scripts/check_exhibits.py --chromium <path> --site _site --out /tmp/b.json` (PASS) and `node scripts/check_exhibit_logic.mjs` (PASS).
  3. Save screenshots at 1280 and 390 px into the SDD folder.
  4. Run the full suite (OK).
- [ ] **Step 5:** Commit with the message "Show RASID on OPSD".

### Task 8: Landing, docs and records

**Files:**
- Modify:
  - `scripts/build_site.py`, `site/landing.html`, `tests/test_site.py`;
  - `README.md`, `README.ar.md`, `CHANGELOG.md`, `CLAUDE.md` (the commands only);
  - `docs/RELEASE_3.2.md`, `docs/DATA_LICENSES.md`, `docs/QUALIFICATION.md`, `docs/TECHNICAL_GUIDE.md`;
  - `reports/application-tests.txt`, `reports/browser-checks.json`, `SHA256SUMS`.

**Interfaces:**
- Produces `opsd_facts(record) -> dict`, with keys:
  - `opsd_days`, `opsd_days_wh` (warehouse) and `opsd_days_rb` (research building);
  - `opsd_share_cand`, `_lo`, `_hi` and `opsd_share_chronos`, `_lo`, `_hi` (pooled share of achievable value, risk-aware policies);
  - `opsd_cand_naive`, `_lo`, `_hi` (pooled candidate − seasonal_naive, % of the daily no-battery cost) with `opsd_cand_naive_en` and `opsd_cand_naive_ar` phrases from the interval sign;
  - `opsd_chronos_ridge_en` and `opsd_chronos_ridge_ar` (the pooled Chronos − ridge MAE reading);
  - `opsd_verified` and `opsd_planned` (accepted and planned policy-days).

  Guards (fail closed):
  - the record is sealed, with a passing gate, an identical repeat in a second process, and not quick;
  - the target days equal the sum of the per-site rows;
  - every phrase matches its interval;
  - no currency words in the block.
- Produces a landing block, "Real load: RASID on two business sites", in English and Arabic. It uses only these keys, links to `report/opsd/`, says "reconstructed" and "illustrative tariff units", and gives the attribution in one line.

- [ ] **Step 1: Write the failing tests** in `tests/test_site.py`:
  - the keys equal values computed from the record;
  - each guard fails closed (a subTest per guard);
  - the block appears in both languages;
  - the block contains no currency words.
- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run. Expected: PASS.
- [ ] **Step 3: Docs.** Write and update the documentation, with every figure from `reports/opsd/`:
  - `docs/RELEASE_3.2.md` gains a RASID section with the protocol and seal, the pre-seal inspection, the gate, the results with intervals, the refusals, the limits (two sites in one city, 2015–2017, reconstructed load, small solar share, an illustrative tariff scaled to each site, a hypothetical battery, no currency) and the attribution.
  - `docs/DATA_LICENSES.md` gains an OPSD section.
  - The README and README.ar gain rows, and their "data is generated" lines name OPSD too.
  - The CHANGELOG 3.2.0 entry is extended, and QUALIFICATION gains a row.
  - `docs/TECHNICAL_GUIDE.md` gains a section with the fetch and evaluate recipe, time, peak memory and disk.
  - The CLAUDE.md Commands block gains `python scripts/fetch_opsd.py --out var/opsd` and the evaluate line.
- [ ] **Step 4: Records.**
  1. Run the full suite with `-v` in `.venv`, the 4 loader tests in `.venv-hf`, and the suite on Python 3.13 (`var/venv313`).
  2. Rewrite `reports/application-tests.txt` in its header format.
  3. Regenerate `reports/browser-checks.json` with `--site _site`.
  4. Regenerate `SHA256SUMS` last, then run `sha256sum -c SHA256SUMS`. Expected: all OK.
- [ ] **Step 5:** Commit with the message "Publish the OPSD evaluation on the site and in the docs".
