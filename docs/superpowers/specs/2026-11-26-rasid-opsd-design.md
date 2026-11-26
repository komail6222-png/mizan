# RASID on real load: two business sites from Open Power System Data

Design, 26 November 2026. Phase B, part 2. Branch `rasid-opsd`, from `main` at 2a6c70a.

## Intent

MIZAN is the owner's CV-defining AI-engineering project. The owner wants it to be a real product that companies would use, measured honestly, with "the strongest thing available". Phase B replaces generated evidence with real public data. Part 1 (BASIRA on VisA, merged as komail6222-png/mizan#3) did this for inspection. This part does it for RASID, the battery planner: a forecaster, a risk-aware planner and an independent verifier.

**The question:** on real business sites, does RASID plan well? The owner chose to measure this **without currency**. The measures are:
- the share of the battery's achievable value each policy captures;
- its cost against a hindsight plan;
- its worst days;
- how many of its plans the verifier accepts.

All of these are under RASID's illustrative tariff, scaled to each site. Euros would need a real German tariff and a battery cost. That is left to a later, separately labelled analysis.

Today RASID is evaluated on a generated commercial site over 60 days. Release 3.0 states the honest limit: 60 generated days cannot settle whether the forecast is worth money. Real data offers several hundred days per site.

## Goals

- Evaluate the **unchanged** RASID forecaster, planner and verifier on the real load and solar of two business sites, over every day the data supports. Report per site and pooled, with intervals.
- Compare three forecasters on the same days:
  - the shipped ridge forecaster;
  - the seasonal baseline;
  - Chronos-Bolt-tiny, the 3.1 pin.
- Plan seven policies on each day:
  - the five shipped policies: candidate (risk weight 0.35), candidate_risk_neutral, seasonal_naive, perfect_foresight (hindsight reference) and no_battery;
  - chronos and chronos_risk_neutral, as in 3.1.
- **Seal:** fix the protocol in code and seal it before any site day is forecast or planned.
- **Fidelity gate:** the new harness must reproduce RASID's published generated-site results before any site result is kept.
- **Repeat:** repeat the whole evaluation in a second process.
- **Publish:** a results page, a guarded block on the introduction, and the docs, each attributed.

## Non-goals

- Euros, a real tariff, battery costs or payback. These are a possible later analysis.
- Changing the forecaster, planner, verifier or tariff structure. Engine code changes only to fix a reproduced failure (CLAUDE.md).
- Homes, schools or the crafts business (see Data).
- Real electricity market prices: the ENTSO-E licence is unclear.
- A release. This work joins 3.2.0, which is still unreleased; a release is a separate decision of the owner's.

## Anti-goals (failure even if it runs)

- Any figure stated in currency, or any wording that implies money saved.
- Scoring a day that contains interpolated, copied or straight-line-filled data.
- Choosing a battery size, tariff, day rule or policy after seeing any forecast or plan on site data.
- Calling the reconstructed load "measured" without saying how it was reconstructed.
- Presenting two sites in one city as evidence about businesses in general.
- Any sign that the data is MIZAN's own, or missing the attribution.

## Data (inspected before the seal; disclosed)

**Source.** Open Power System Data, *Data Package Household Data*, version 2020-04-15. File `household_data_60min_singleindex.csv`: 15,654,559 bytes, SHA-256 `15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60`. URL `https://data.open-power-system-data.org/household_data/2020-04-15/household_data_60min_singleindex.csv`. Licence CC BY 4.0. Primary data from the CoSSMic project, Konstanz, Germany. The data is not committed; `scripts/fetch_opsd.py` downloads it into the git-ignored `var/opsd/` and checks the size and SHA-256.

**Meaning.** The values are cumulative meter readings in kWh: every series is non-decreasing. Each row is labelled with the start of its hour but holds the reading at that hour's end. OPSD's processing takes the last reading in each hour, and the data confirms it: with that reading, summer solar at both sites peaks in the hour 11:00–12:00 UTC, around Konstanz's solar noon (about 11:23 UTC). With the other reading, it would peak an hour early. So the energy of the hour that starts at t is reading(t) − reading(t − 1h), and it also equals that hour's mean power in kW. Timestamps are given in UTC and in Central European time. A column `interpolated` names the columns that were filled at each timestamp. The package says gaps were filled linearly or with data from earlier days.

**The two sites:**

| Site | Building | Columns used | Load (kW): mean, max | Solar max (kW) | Solar share of energy |
|---|---|---|---|---|---|
| `industrial1` | warehouse | `grid_import`, `pv_1`, `pv_2` | 33.9, 144.9 | 8.8 | 3.4% |
| `industrial3` | research-institute building | `grid_import`, `pv_facade`, `pv_roof` | 81.9, 235.6 | 17.2 | 2.8% |

**Clean days at inspection** (rules 1–3 below):
- `industrial1`: 601 clean days, November 2015 to October 2017.
- `industrial3`: 443 clean days, February 2016 to June 2017. Another 30 complete, unflagged days have four or more identical non-zero hourly imports, which looks like an unflagged straight-line fill.
- **Target days** (rule 5), counted from the data rules alone: 547 at `industrial1` in 7 runs, and 276 at `industrial3` in 19 runs, so **823 in all**. A 14-day look-back would leave 517 and 169.

These are inspection figures. The sealed code counts again, and the evidence records its own counts, which the docs cite.

**Why only these two:**
- **The crafts business** (`industrial2`) has its own battery, solar readings of up to 667 kWh in one hour, and unmetered export.
- **The schools** have no solar and short coverage.
- **The homes** are a few kW in size. Two of the three with export meters have hundreds of hours whose reconstructed load is negative.

**Load reconstruction.** Neither site has an export meter, so load = grid import + solar holds only if the site never exports. The data supports this. In 3,590 sunny hours at `industrial1` and 2,814 at `industrial3`, grid import never fell below 2% of the median load. The protocol makes this a data gate (below). Solar is small at both sites, so the battery's value comes mainly from shifting load into cheap hours and from cutting the monthly peak.

**What was looked at before the seal.**
- per-site coverage, the flags, and the copy and straight-line checks;
- the export check;
- load and solar summaries, and solar share;
- the hour alignment, by when solar peaks;
- target-day counts under a 7-day and a 14-day look-back.

No forecast, plan or cost was computed on site data. The inspection scripts are kept with the working notes, and the release notes describe the inspection.

## Approaches

- **A, a full evaluation in the VisA style (chosen by the owner).** It has:
  - a sealed protocol module and its own evaluation harness;
  - three forecasters, the seven policies, a fidelity gate on generated data and a second-process repeat;
  - a pinned-hash fetch, a run log, a results page, a landing block and docs.

  It reuses the shipped functions (`forecast`, `solve`, `verify`, `realized`, `decision_summary`, `paired_comparison`, and the 3.1 Chronos scenario rule) with no engine change.
- **B, a thin adapter.** Convert the data and call `energy_evaluation()` per site. Rejected: that function is fixed to the generated site (an 80 kWh battery for a 67 kW site, UTC days, fixed dates). Changing it would change shipped code. It would also have no seal, no gate and no Chronos.
- **C, forecasting only.** Dropped: it contradicts the owner's choice to measure planning.

## Protocol (sealed in code)

`mizan/opsd.py` holds `PROTOCOL`, a dictionary of every choice below, and `SEALED_DIGEST`, its digest. The seal is the commit that introduces the digest (the rule `check_seal` now enforces). Any change to the protocol after that is a new, separately labelled analysis.

**1. Hourly energy.** For each site's three columns, the energy of the hour labelled t (that is, [t, t+1h) in UTC) is reading(t) − reading(t − 1h). It is missing when:
- either reading is missing, or the two are not exactly one hour apart;
- the difference is negative;
- a column used appears in `interpolated` at t or at t − 1h.

**2. Load and solar.**
- Solar = the sum of the site's two solar columns.
- Load = grid import + solar.

**3. Clean local day.** A day in Europe/Berlin is clean when:
- every one of its hours has all three energies;
- it is not identical, hour by hour, to the previous local day in grid import;
- its grid import has no run of four or more identical non-zero hourly values.

Energies are compared after rounding to 1e-6 kWh. A **clean hour** is an hour of a clean day. History, the Chronos context, the no-export gate and the grid limit use clean hours only, so a copied or filled day never feeds a forecaster.

**4. No-export gate.** Over all clean hours, no hour may have solar above 20% of the site's 99th-percentile solar while grid import is below 2% of the site's median load. If any does, the run stops before anything is forecast. Otherwise load = import + solar would not hold. The gate's counts are recorded.

**5. Target day.** A target day is a clean day that meets all of these:
- it has 24 hours, so the daylight-saving changeover days are excluded and counted;
- each of the 7 local days before it is clean. Seven gives Chronos at least a full week of context, and it gives the ridge forecaster its 24-hour lag. A 14-day look-back would cut the research building from 276 days to 169;
- the ridge forecaster accepts it. A refusal of its own (WARMUP, CALIBRATION, MISSING_LAG) is counted by cause, and the day is not a target.

A site's target days form runs of consecutive days.

**6. Forecast inputs per target day.**
- **History:** every clean hour known before the origin (local midnight) within the previous 300 days, with `known_at` one hour after the hour. This is the vintage rule of the shipped evaluation, and the 300 days keep it within `records`' limit.
- **Ridge:** `forecast(rows, origin, grid, 4)`, as shipped, over the 24 local hours.
- **Seasonal baseline:** its `seasonal_naive` path.
- **Chronos:** the contiguous run of clean hours ending at the hour before the origin, at most 2,048 hours, through `models.chronos_forecaster`, under the 3.1 `SCENARIO_RULE` (five equally weighted scenarios, solar clipped at zero).

**7. Battery and tariff per site.**
- **Scaling:** RASID's `examples.settings(24)` with every quantity scaled by s = (the site's mean load over its first 28 clean days) ÷ the generated site's mean load. The code computes the generated mean from `examples.electricity(102)`, the 102 days the shipped evaluation reads: 67.214 kW. Sizing from the first 28 days uses only data from before the evaluation, as a real deployment would.
- **What scales:** capacity 80·s kWh, power 20·s kW, initial and terminal charge 40·s kWh.
- **Grid limit:** twice the site's highest clean hourly load in the record, so it never binds. It never binds in the generated evaluation either.
- **What doesn't scale:** prices per kWh and per kW, risk weight 0.35, β 0.90, efficiencies and the cycle limit.
- **Time-of-use price:** by local hour, as shipped: 0.34 from 16:00 to 22:00, 0.07 before 07:00, and 0.16 otherwise.

**8. Carried state.** Each policy carries its own battery charge and month-to-date peak from day to day, as shipped.
- The month-to-date peak resets at each local month's start, as shipped.
- At the start of each run of target days, after a gap, both reset to their shipped starting values: the true state is unknown after a gap. Those values are the initial charge (no_battery at zero) and a peak of zero.

**9. Measures per site.**
- **Forecast accuracy** on load, as in 3.1:
  - MAE of the ridge point, the baseline and the Chronos median;
  - pinball loss and truncated CRPS on the shared levels 0.1, 0.5 and 0.9;
  - 80% coverage.
- **Decision quality per policy** (`decision_summary`): total cost, cost against hindsight, battery value, share of achievable battery value, worst day, mean of the worst 10% of days, verified plans.
- **Paired daily differences**, with the moving-block bootstrap of rule 10 (4,000 draws, seed 2026). The comparisons are:
  - candidate − seasonal_naive;
  - candidate_risk_neutral − seasonal_naive;
  - candidate − candidate_risk_neutral;
  - chronos_risk_neutral − candidate_risk_neutral;
  - chronos_risk_neutral − seasonal_naive;
  - chronos − candidate.
- **Paired accuracy differences** for ridge − baseline and Chronos − ridge MAE, with the same bootstrap.
- **Share of achievable battery value** for each policy, with an interval from the same bootstrap: each draw takes the ratio of the resampled sums.
- **Point values only:** accuracy levels, cost against hindsight, worst day, the mean of the worst 10% of days, and plan counts. The inferential claims rest on the paired differences and the share of value.

**10. The bootstrap, and pooling.**
- **Within a site:** a block is 7 consecutive target days inside one run. A run shorter than 7 days is one block of its own length. Each draw picks blocks uniformly from all such blocks, concatenates them until it reaches the site's number of target days, and truncates. A block never spans a gap.
- **Pooled:** the sites differ in size, so pooled results are unit-free, and each site counts equally, as VisA weighted its classes equally. The unit-free measures are:
  - each day's cost difference divided by that site's mean daily no-battery cost;
  - MAE divided by that site's mean load;
  - the share of achievable value as it is.

  Each draw resamples both sites independently and averages their two means. The normalising means are computed once from all target days and held fixed in every draw (stated on the page).

**11. Readings.** "Resolved" means the 95% interval excludes zero. An interval that touches zero is not resolved. Many intervals are reported, with no multiplicity adjustment; the page says so.

**12. Refusals on a target day.** These are counted, not fatal:
- the planner finds no plan (`NO_INCUMBENT` within the shipped 12-second limit);
- the verifier rejects a plan (`PLAN_REJECTED`);
- a planner input is refused, for example a negative Chronos load quantile.

When any of these happens for any policy, the day is dropped for every policy and every forecaster, so the paired rows stay paired. It is recorded by cause and policy, and it ends the run: the next target day starts a new run with reset state. The verifier measure reports accepted plans out of all planned days, rejections included. The repeat run must still be identical. A wall-clock solve limit that changes a plan between processes shows as a non-identical repeat, and the pages then refuse the record.

## Fidelity gate (before any site day)

1. **The generated site, ridge side.** The harness, run on `examples.electricity` with the shipped settings and UTC days (the generated site's own grid), must reproduce `reports/evaluation/rasid.json` under the 3.1 `rasid_gate` tolerances:
   - MAE within 1e-9 kW;
   - candidate totals within 5e-4 relative;
   - other totals within 1e-6 relative;
   - plan counts exact.
2. **The generated site, Chronos side.** The same run must reproduce the Chronos side of `reports/hf/rasid.json`:
   - the Chronos MAE, pinball loss, CRPS and coverage within 1e-9;
   - the chronos and chronos_risk_neutral totals within 1e-6 relative;
   - plan counts exact.

A failed gate exits with code 2 and writes nothing. Both gates run only in a real run, as for VisA.

## The run

`scripts/evaluate_opsd.py`, in `.venv-hf` (Chronos needs torch). It follows the VisA runner's discipline:
- `--seal` names the sealing commit, and a changed protocol is refused.
- The data file is verified by SHA-256 before anything is read.
- The gate runs first.
- The evidence directory `reports/opsd/` is written only with `--repeat`, which reruns everything in a second process and compares the records, VOLATILE fields excepted.
- `--child` runs only when the parent starts it.
- Every run logs a "started" entry before the first site day is forecast and a "finished" entry after, in `reports/opsd/runs.json`.
- The record keeps the tree state at the start.

The generated 60-day evaluation with Chronos took seconds in 3.1, so no dry run is needed. The gate's own timing, on 60 generated days, is logged, and the full run's time and peak memory are recorded.

## Outputs

- `reports/opsd/rasid.json`, the evidence: per site and pooled; per-day rows; the protocol digest and seal; the gate; the repeat; the environment; provenance; the data file's SHA-256 and URL.
- `reports/opsd/runs.json`, the run log.
- `reports/opsd/index.html`, the results page, served at `report/opsd/` and linked from the evaluation report.
  - **Per site:** the sizing (s, battery, limit), day counts (clean, excluded and why, target, runs), forecast accuracy with intervals, decision quality for the seven policies, paired comparisons with readings, and verified plans.
  - **Pooled:** the unit-free measures.
  - **Also:** the attribution, a line "illustrative tariff units, no currency", and a line saying the load is reconstructed as import + solar under the no-export gate.

  It must stay under 60,000 characters, make no external request, and contain no typed numbers: everything comes from the record. Every reading is computed from interval signs.
- The introduction gains a bilingual block, "Real load: RASID on two business sites". `scripts/build_site.py` reads its figures from the record and guards every sentence (the seal, the gate, the repeat in a second process, the day counts, each stated direction against its interval). Its phrasing comes from the evidence, so a "not resolved" result reads as not resolved.
- **Docs:**
  - `docs/RELEASE_3.2.md` gains a RASID section (3.2 stays unreleased).
  - `docs/DATA_LICENSES.md` gains the OPSD attribution.
  - The README and Arabic README gain a row, the CHANGELOG 3.2.0 entry is extended, and QUALIFICATION gains a row.
  - `docs/TECHNICAL_GUIDE.md` gains the fetch and evaluate recipe, time, memory and disk.
  - The CLAUDE.md commands gain the fetch and evaluate lines.
- **Tests:**
  - the data rules on a generated stand-in file (cumulative readings with flags, copies, a fill, an export hour, a daylight-saving day);
  - the gates, the runner's refusals, the log, and the page and landing guards.

  The suite never reads the real file.
- **Records:** the test record, the browser checks, SHA256SUMS.

## Owner decisions (confirmed with this design)

1. The CLAUDE.md invariant names VisA as the one real dataset. It must also name this OPSD package: real public measurements under CC BY 4.0, attributed, never presented as MIZAN's own data, and with the load described as reconstructed.
2. The CLAUDE.md commands gain the OPSD fetch and evaluate lines.
3. The download of the 60-minute file was approved on 26 November 2026. The design downloads nothing else.

## Left to the builder

- Module layout within `mizan/opsd.py` and `mizan/opsd_evaluation.py`, and how the harness shares code with `mizan/hf_evaluation.py` without changing its behaviour.
- The stand-in data generator for tests.
- The page's layout, within the evaluation report's style.
- The exact wording on the page and in the docs, under the anti-goals above.
