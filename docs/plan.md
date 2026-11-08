# Stuff+ Model — Technical Design & Implementation Plan

Living design doc for the project. Work and progress are tracked in [GitHub Issues](https://github.com/nkbelow/stuff-plus/issues); decisions in [`adr/`](adr/); the silver-layer spec in [`specs/silver.md`](specs/silver.md); a step-by-step explainer in [`walkthrough.md`](walkthrough.md); what Savant's columns mean in [`data-guide.md`](data-guide.md). `~/fantasy_baseball` consumes the scores later.

---

## 0. What I found before planning

- **Existing repo:** `~/fantasy_baseball` already has uv + Python 3.12, DuckDB/Parquet, pybaseball, ruff, pytest, and a raw→transform layering convention. It ingests Savant *leaderboards* (season aggregates), not pitch-level data. → **Recommendation: new repo `~/stuff-plus`** that reuses the same conventions. Fantasy can read its gold score tables later. The two projects have different lifecycles, and ML experiment state (MLflow runs, models) shouldn't clutter the fantasy pipeline.
- **The calendar changes your split.** It's November 2026, so **the 2026 regular season is complete**. That gives a true future season to hold out (§3).
- **Statcast schema changes in 2026 (from the CSV docs):** `plate_x`/`plate_z` moved from front-of-plate to **middle-of-plate**, and `sz_top`/`sz_bot` became **ABS-defined** (the ABS challenge system started in 2026). Location columns aren't comparable across 2025→2026. We exclude location from Stuff features anyway, but any VAA-at-plate calculation or zone-based EDA has to account for it. Called-strike labels also shift slightly because of challenges.
- **Leaky columns exist in the export:** `pitcher_days_until_next_game`, `post_*_score`, `delta_*`, `events`, `des`, the bat-tracking columns, etc. A feature allow-list is mandatory (§4B).
- **pybaseball is lagging on maintenance.** Its `statcast()` is a thin day-chunked wrapper over the Savant CSV endpoint. Since you want raw-source preservation, I recommend a small direct fetcher with pybaseball as a cross-check (§6).
- **Public model landscape** (tjStuff+, aStuff+, FanGraphs Stuff+, PitchingBot, PLV, BP StuffPro): two families exist. Older models do **regression on a denoised expected run value** (original FanGraphs Stuff+, tjStuff+, aStuff+ v1/v2). Newer ones use **outcome classification → expected run value** (current FanGraphs Stuff+, PitchingBot, PLV, BP StuffPro). Typical features: velo, IVB, HB, spin, spin axis, extension, release height/side, plus differences from the primary fastball. All exclude location. Scaling is usually mean 100 / SD 10.

---

## 1. The decisions that matter, ranked

1. **The target definition.** Everything else follows from it (§4A).
2. **Pitcher memorization via release-point fingerprints.** This is the most likely way to fool yourself (§4C).
3. **The split protocol and which seasons stay untouched** (§3).
4. **How location is handled.** It's excluded from features but it still drives the label (§4A, option 3).
5. **The scaling and reference season for "100"** (§4D).

Infrastructure is the least risky part for you. Spend your learning hours on 1–4.

---

## 2. Recommended MVP methodology (one paragraph)

Model only four-seam fastballs (`pitch_type == 'FF'`), regular season, 2022–2026. Build a **denoised, context-neutral pitch run value label (`xRV`)**. Called balls/strikes, whiffs, fouls and HBP get the league-average run value of their outcome class. Balls in play get the average run value of their exit-velocity × launch-angle bucket, not their actual result. Both value tables are fitted on training seasons only. Train **one LightGBM regressor (L2 loss)** on handedness-normalized physical features only: no location, no count, no pitcher ID. Early-stop on 2024 and test pitch-level accuracy against constant and linear baselines. Aggregate predictions to pitcher-seasons, convert to a fixed-reference **100 ± 10 scale where higher = better**, and judge the scores mainly on **reliability and next-season predictive value beyond observed results**. 2026 is touched once, at the end.

---

## 3. Data window and splits — challenging the 2023/24/25 plan

**Problems with train-2023 / val-2024 / test-2025:**
- 2023 alone (~220–240k four-seamers, rough estimate) is enough for the pitch model. But you can't run a clean *pitcher-level predictive* test, which needs score-season → outcome-season pairs, without reusing seasons.
- It ignores 2026, which now exists and is the most honest "future" holdout.

**Seasons to include and exclude:**
- **Exclude 2020 and earlier:** 60-game season. Pre-2020 data is TrackMan, which measures differently from Hawk-Eye.
- **Exclude 2021 (or use only post-June 21):** the mid-season sticky-substance crackdown shifted spin rates. That's a distribution break inside the season.
- **2022 is fine** even though 2023 brought the pitch clock and shift ban. The shift ban mostly affects BIP *results*, and our label neutralizes that by using EV/LA instead of hit/out. Check during EDA for a velo/IVB level shift between 2022 and 2023.
- **2026 caveats:** ABS challenges and redefined plate/zone columns. It's usable as a final test, but compare 2025 vs 2026 label distributions (called-strike rate, etc.) in EDA, *without* looking at model performance.

**Recommended protocol (rolling origin):**

| Phase | Train | Early stop / tune | Score | Outcomes used |
|---|---|---|---|---|
| Development (iterate freely) | 2022–2023 | 2024 | 2024 | pitch-level on 2024; **2024 scores → 2025 outcomes** |
| Final (run once, freeze before running) | 2022–2024 | fixed hyperparams, n_trees scaled from dev | 2025 | pitch-level on 2026 (Model B applied); **2025 scores → 2026 outcomes** |

2025 serves as a *development outcome season* (pitcher aggregates only). 2026 is never looked at until the final run. Write the final-run config and acceptance thresholds into git **before** running it. That's your pre-registration.

If 2022 turns out to be problematic, fall back to train 2023, val 2024, and keep the rest the same.

---

## 4. Critical modeling decisions

### 4A. What should the model predict?

Signal-to-noise framing: one four-seamer's outcome is almost pure noise relative to its physics. Expect pitch-level R² of roughly 0.5–3%. That isn't failure. The value appears when thousands of pitches are averaged. **Every target choice is a way to remove noise the pitcher doesn't control while keeping the signal they do.**

**Option 1 — Regress raw `delta_run_exp`.**
- *Validity:* it estimates E[ΔRE | physics] under the observed context mix. Unbiased only if context is independent of physics, which it isn't. Relievers throw harder *and* pitch in different base/out/leverage states, and fastballs are over-thrown in hitter's counts.
- *Noise:* worst of the three. It includes base-out state, defense, park, sequencing, and BIP luck. Heavy-tailed (a HR is about +1.4 runs, a ball about +0.05), so squared loss is dominated by a few hundred homers.
- *Bias:* count mix, leverage, park, defense quality.
- *Data / difficulty:* trivial; the column already exists.
- *Stuff+ intent:* poor. It measures results, not stuff.

**Option 1b (recommended MVP) — Regress a denoised, context-neutral `xRV`.**
- Non-BIP pitches: xRV = mean `delta_run_exp` for that outcome class {ball, called strike, swinging strike, foul, foul tip, HBP, …} in the training seasons. That removes base/out, score and leverage.
- BIP: xRV = mean `delta_run_exp` within the EV × LA bucket (e.g. 3 mph × 4°, with smoothing/minimum counts) in the training seasons. That removes defense, park and the "hit 'em where they ain't" variance.
- Count handling: **MVP uses count-neutral class values** (averaged over counts). That's simplest, but it means a whiff is worth the same in 0-0 as in 0-2. Variant to try later: condition the values on count, and reweight training rows to a fixed league count distribution.
- *Validity:* good, as long as the value tables are fit on train only and treated as part of the label definition.
- *Noise:* much lower than option 1; BIP noise shrinks the most.
- *Bias:* location and count mix are still implicitly in the label (see option 3); batter quality faced is small.
- *Difficulty:* low. One extra gold table plus one regressor.
- *Intent:* close to the original FanGraphs Stuff+ and tjStuff+.

**Key insight:** with squared loss, a regressor on outcome-value labels estimates E[xRV | x] = Σ_k P(k | x)·v_k. That's the *same quantity* a classifier-plus-mixing approach estimates. The two differ in statistical efficiency, interpretability, and flexibility (you can change v_k without retraining), not in what they target. So 1b is a scientifically sound MVP, and option 2 is a later refinement, not a fix.

**Option 2 — Outcome classification → expected run value (v2 milestone).**
- Hierarchical sub-models: P(swing), P(whiff | swing), P(foul | contact), and E[xRV_bip | in play] (a regression).
- E[RV] = Σ P(outcome) × v(outcome).
- *Validity:* best. Each sub-model learns a cleaner binary signal. For example, whiff | swing is *strongly* driven by fastball shape, while P(swing) and called-strike odds are driven mostly by location and count, which Stuff excludes. That makes those sub-models nearly noise, which is itself informative.
- *Noise:* lowest per component. Calibration matters because probabilities are multiplied by values.
- *Difficulty:* 3–4 models, a calibration check for each, and a combiner.
- *Intent:* matches current FanGraphs Stuff+, PitchingBot and StuffPro.
- *Why not first:* it adds four pieces that can be subtly wrong before you have one that works. Build it in week 9–10 and compare it head-to-head with 1b on the same evaluation harness.

**Option 3 — Separate stuff from context and location ("integrate out location").**
- Train a model that *includes* location (plate_x/z relative to the zone) and count. Then score each pitch's physics by **averaging predictions over a fixed league reference distribution of locations and counts** (Monte Carlo / partial dependence).
- *Why it matters:* in options 1/1b, leaving location out doesn't make the model location-blind. It *marginalizes* over each pitch's actual locations. If a physical feature such as release point correlates with a pitcher who has great command, the model credits the command to "stuff" (omitted-variable bias). Option 3 asks the counterfactual directly: "how good is this shape if thrown to average spots?"
- *Noise:* similar to 1b. *Bias:* lowest. *Difficulty:* high (scoring cost, the 2026 plate_x redefinition). *Intent:* the most faithful to what Stuff+ *claims* to measure.
- → Future extension or stretch experiment.

**Recommendation:** MVP = **1b**. v2 = **2**, with 1b as the baseline. Stretch = **3**.

### 4B. Features

Normalize handedness so **arm side is positive** for both hands. Every x-dimension feature is multiplied by −1 for one hand. Verify the sign in EDA: RHP four-seamers should show *negative* raw `pfx_x` (catcher's perspective). Write a unit test with a known pitcher.

| Group | Feature | Baseball meaning | MVP? |
|---|---|---|---|
| Raw physical | `release_speed` | Velocity. The biggest single driver of FF quality; less reaction time. | ✅ |
| | `release_spin_rate` (CSV docs call it `release_spin`; verify the name) | Raw spin. Only matters through the movement it creates. Partly redundant with movement but captures "efficiency" indirectly. | ✅ |
| | `spin_axis` (mirror: 360 − axis for LHP) | Direction of spin; 180° = pure backspin. | ✅ |
| | IVB = `pfx_z`×12 (in) | "Rise" relative to a spinless ball. High IVB → swings under it. | ✅ |
| | HB = `pfx_x`×12, arm-side + | Run. | ✅ |
| | `release_pos_z` | Release height. Low release + high IVB = flat approach angle = "invisible fastball". | ✅ |
| | `release_pos_x` (mirrored) | Release side. Width creates angle and deception. | ✅ (ablate) |
| | `release_extension` | How far down the mound the ball is released. Perceived velocity. | ✅ |
| | `arm_angle` | Shoulder-to-ball angle. Contextualizes how "unexpected" the movement is. Verify which seasons it exists for. | ✅ if present 2022+ |
| Derived physical | movement magnitude & angle | Polar form of (HB, IVB). | ✅ |
| | IVB relative to expected-given-arm-angle (fit on train) | "Ride over expectation", a key modern fastball concept. Trees can learn it, but explicit is easier to interpret. | v1.1 experiment |
| | spin/velo ("Bauer units") | Spin normalized for velo. | optional |
| | `vx0, vy0, vz0, ax, ay, az` | Raw trajectory components. Alternatives to pfx; redundant. | ❌ (redundant) |
| | `effective_speed` | A function of velo + extension. Redundant. | ❌ |
| | VAA at plate | Approach angle. **Depends on location** (high fastballs are flatter), and the plate definition changed in 2026. | ❌ in Stuff; maybe "VAA adjusted for height" later |
| Pitcher-relative | FF velo/IVB vs the pitcher's own baseline | Within-pitcher variation (fatigue, "good day"). For *secondaries* this becomes "Δ vs primary fastball", the key feature. | ❌ MVP; essential for secondaries |
| Contextual | `stand` (batter hand), count, outs, base state, inning, `n_thruorder_pitcher`, park, batter, catcher, umpire | Real effects, but they belong to the situation, not the pitch. | ❌ (stand: later, train with it and score averaged at the league L/R mix) |
| Excluded — location | `plate_x`, `plate_z`, `zone`, `sz_*` | That's Location+/Command, not Stuff. | ❌ |
| Excluded — identity | `pitcher`, `player_name`, team | See below. | ❌ |
| **Forbidden — leakage** | `description`, `type`, `events`, `des`, `bb_type`, `launch_*`, `hit_*`, `estimated_*`, `woba_*`, `babip_value`, `iso_value`, `delta_*`, `post_*`, bat tracking (`attack_angle`, `swing_path_tilt`, …), `*_days_until_next_game` | Outcome or future information. | 🚫 enforced by allow-list + test |

**Pitcher identity:** exclude it. Including it lets the model explain outcomes through "who" instead of "what", which is the opposite of Stuff. Note, though, that **excluding the ID column doesn't stop the model from identifying pitchers.** Release height + side + extension + arm angle + velo is close to a fingerprint, and a deep tree can isolate a single pitcher and learn their outcome mean, which includes their command, deception and luck. Mitigations: large `min_data_in_leaf` (hundreds+), out-of-time evaluation, pitcher-grouped CV for tuning, and an **ablation with release features removed**. If in-sample pitcher scores correlate with same-season results far more than out-of-sample ones do, you're memorizing.

**Point-in-time pitcher-relative features (for secondaries later):** compute the pitcher's fastball baseline from **pitches strictly before the current game** (expanding mean within the season, falling back to the prior season and then the league mean for debuts). Implement it as an as-of join in DuckDB: `ASOF JOIN ... ON pitcher AND game_date > baseline_date`. Most public models use full-season means, which is mild look-ahead. Physical means stabilize fast, so the practical impact is small. Do it correctly anyway: it's cheap, and it's the habit you want.

**Pitch classification:** `pitch_type` is Savant's algorithmic label. It can change between seasons for a pitcher, and Savant may reclassify history retroactively (so record the download date in bronze). FF vs FC vs SI boundaries are fuzzy. In silver, add DQ flags (e.g. an FF under 85 mph, or a pitcher's FF cluster jumping between seasons). In EDA, measure how much volume is borderline. Don't reclassify for the MVP.

**Missing data:** drop rows missing any core physical feature from training and scoring, and report the rate by season and month (expected <1–2%; spikes indicate tracking outages or park issues). Never impute the label. LightGBM handles NaN natively, but we only rely on that for optional features.

### 4C. Evaluation framework

**Baselines (all on the same splits and labels):**
1. Constant = train-mean xRV.
2. Velocity-only linear regression.
3. Ridge on all MVP features, plus a version with a few hand-made interactions.
4. LightGBM.

If LightGBM can't beat ridge meaningfully, that's a finding, not a bug.

**Level 1 — pitch-level accuracy (2024 dev, 2026 final):** RMSE and R² vs constant; **calibration by decile** (mean predicted vs mean actual xRV per predicted decile — should hug the diagonal); a component check: AUC for whiff | swing using predicted xRV as the score. Bootstrap confidence intervals **by game or pitcher, not by pitch**, because pitches are correlated.

**Level 2 — pitcher-level reliability:**
- *Split-half:* odd vs even games within a season, corr(Stuff_odd, Stuff_even), Spearman–Brown adjusted.
- *Year-over-year:* same pitcher, season N vs N+1.
- *Stabilization curve:* reliability vs number of pitches. This sets the minimum sample (§4D).

**Level 3 — future predictive value (the real test):**
- Outcomes in N+1: FF whiff rate, FF xRV/100, pitcher K%, K–BB%, and an ERA estimator (SIERA/xFIP) if obtainable. Don't use ERA itself; too noisy.
- Compare predictors from season N: our Stuff+, *observed* FF xRV/100, observed whiff%, velo alone, FanGraphs FF Stuff+ (`Stf+ FA`), and tjStuff+ if you can get it.
- **The usefulness test is incremental:** regress outcome_{N+1} ~ observed_N + Stuff_N and check whether Stuff_N adds R² or a significant coefficient. Also stratify by sample size. Stuff should beat observed results at small N (it stabilizes faster) and converge at large N.
- *Generalization:* a subgroup of pitchers with **no training-season pitches** (2024/2025 rookies). If Stuff+ works only for pitchers seen in training, it's memorizing.

**Level 4 — bias and calibration slices:** residual mean by hand, arm-angle bucket, velo bucket, release height, starter vs reliever (relievers throw harder *and* face batters once, which confounds velo and context), month (fatigue, weather), and park (Coors affects movement).

**How to tell "useful" from "just correlated with past performance":** a model trained on outcomes will always correlate with *same-season* outcomes, partly through memorization. That correlation proves nothing. What counts: (a) out-of-sample pitchers, (b) predicting **next** season, (c) **incremental** value over the observed stat it's built from, (d) faster stabilization. Report all four.

### 4D. From model output to a score

- Model output: predicted xRV per pitch, in runs. **Negative = good for the pitcher.**
- **Reference set:** all FF in a fixed reference season (2024) scored by the frozen model. Compute μ_ref and σ_ref of *pitch-level* predictions.
- **Score:** `Stuff = 100 + 10 × (μ_ref − pred) / σ_ref`. The sign is flipped so **higher is always better**. 100 = the average 2024 four-seamer's predicted run value; 110 = one SD better.
- Why not FanGraphs-style ratio scaling (100 × pred/mean)? Run values are centered near 0, so ratios blow up. Use z-scores.
- **Pitcher aggregation:** the pitcher's FF score is the mean of pitch-level scores. That's linear, so it's equal to scaling the mean prediction. Pitcher means are naturally compressed compared with pitch scores, and that's correct. Report N alongside every score.
- **Minimum samples:** provisional 100 FF to display and 250 to evaluate. Replace these with values read off the stabilization curve (≈0.7 reliability).
- **Normalization across types (later):** score each type against its own reference (100 = average slider) and also publish raw xRV/100 so types can be compared.
- **Scaling fixed across seasons?** Yes by default: same model version and same reference season. League-wide velocity growth then shows up as a league average drifting above 100, which is real information. A "season-relative" variant can be computed for display.
- **Naming:** call it something distinct (e.g. `ffShape+` or `nbStuff+`) and state in the README that it isn't FanGraphs/Driveline Stuff+, which use proprietary or undisclosed targets, features and scaling.

---

## 5. How LightGBM works

**Boosting in one sentence:** start with a dumb prediction, look at where you're wrong, fit a small tree that predicts *how* you're wrong, add a damped version of it, and repeat a few hundred times.

1. **Initialize:** F₀(x) = mean xRV, i.e. every four-seamer is average.
2. **Gradients:** for L2 loss L = ½(y − F)², the negative gradient is the residual r = y − F(x) and the Hessian is 1. For other losses (log-loss in the v2 classifiers) the gradient is "how much and which way to move the prediction", and the Hessian is "how confident that direction is".
3. **Fit a tree to the gradients.** LightGBM first **buckets every feature into ~255 histogram bins** (e.g. velo 88.0–88.2). For each leaf it sums gradients G and Hessians H per bin, scans the bins, and picks the split with the best gain ≈ G_L²/(H_L+λ) + G_R²/(H_R+λ) − G²/(H+λ). It's one pass over histograms, not over rows. That's why it's fast: much like a GROUP BY bin followed by a scan.
4. **Leaf-wise growth:** instead of growing level by level, it always splits the leaf with the biggest gain, up to `num_leaves`. That gives deep, asymmetric trees that find "97+ mph AND IVB > 18 AND release < 5.6 ft" pockets. It's powerful, and it's exactly how pitcher fingerprinting happens. `min_data_in_leaf` is the brake.
5. **Leaf values:** −G/(H+λ), the average residual in the leaf, shrunk by L2 regularization.
6. **Update:** F_m = F_{m−1} + η·tree_m, where η is the learning rate (~0.03–0.05). Small η means many small steps, which generalizes better.
7. **Early stopping:** after each tree, score the 2024 validation set and stop when it hasn't improved for ~100 rounds. That chooses the number of trees, the most important complexity knob.
8. **Randomness:** `feature_fraction` and `bagging_fraction` subsample columns and rows per tree, which acts as regularization.

**Hyperparameters worth tuning:** `num_leaves` (15–127), `min_data_in_leaf` (200–5000, unusually high because of the noise and the fingerprint risk), `lambda_l2`, `feature_fraction`; `learning_rate` fixed at 0.05 with trees from early stopping. Tune with 15–30 configs of random search, using **GroupKFold by pitcher inside the training seasons** plus the 2024 holdout. Optional experiment: `monotone_constraints` (+velo is never worse), and compare the fit and plausibility.

**SHAP (TreeSHAP):** it splits each prediction exactly into per-feature contributions that sum to (prediction − baseline). Use it for global importance (mean |SHAP|), dependence plots (SHAP of IVB colored by release height, which should show the "flat fastball" interaction), and single-pitcher explanations ("why is X's fastball 118?"). Caveat: correlated features such as velo, extension and spin share credit arbitrarily, and SHAP explains *the model*, not baseball causality.

---

## 6. Data engineering architecture

### Bronze — raw, immutable
- **Source:** a direct fetch from the Savant `statcast_search/csv` endpoint, **one request per game date** (stays under the ~25k-row cap), `hfGT=R|` plus postseason excluded. Rate-limited (1–2 req/s), with retries and exponential backoff. Cross-check a sample against `pybaseball.statcast()` in M1.
- **Storage:** `data/bronze/statcast/season=YYYY/game_date=YYYY-MM-DD/part.csv.gz` holds the exact bytes. DuckDB reads it directly (`read_csv(..., all_varchar=true)`), so no Parquet copy is needed.
- **Manifest:** a per-partition `_meta.json` sidecar (date, URL, fetched_at, http status, row count, sha256, column-list hash, fetcher version), written last; its presence marks the partition complete. The manifest is a DuckDB view over `**/_meta.json`.
- **Idempotency:** write to a temp path, then atomic rename. Re-fetching a date replaces that partition. `ingest backfill --start --end [--force]` skips dates already in the manifest unless forced. Off-days give an empty, valid manifest row.
- **Tests:** a mocked HTTP fixture, the retry path, an idempotent rerun (same hash means no new partition), and schema-drift detection (an alert when the column hash changes).

### Silver — cleaned pitch facts (DuckDB SQL over bronze)
- Detailed design: [`specs/silver.md`](specs/silver.md) (supersedes this section's details).
- Typed columns via an explicit schema contract; `game_type = 'R'` asserted; key **`pitch_id = game_pk-at_bat_number-pitch_number`**, unique per season.
- Savant names and units kept unchanged (units recorded in the schema contract); conversions move to gold.
- **No feature logic here**, so no mirroring. Silver is "the facts, cleaned".
- DQ checks producing a per-season/month report table: null rates per core column, ranges (velo 55–106, spin 500–3800, extension 4–8.5), pitches per game (~250–350), pitch_type vocabulary, and a pitch_id uniqueness check. Warnings vs hard failures are configurable.
- **Tests:** tiny hand-written CSV fixtures → expected silver rows; dedup; type coercion; the DQ rules themselves.

### Gold — ML-ready, versioned
- `gold/labels/xrv_v1`: the outcome-value and EV×LA value tables (fitted on train seasons, saved as artifacts) plus per-pitch `xRV`.
- `gold/features/ff_v1`: mirrored, derived features keyed by pitch_id. The feature list comes from config, and **an allow-list test fails if a forbidden column appears**.
- `gold/datasets/<experiment>`: features ⋈ labels ⋈ split assignment, with a content hash logged to MLflow.
- Point-in-time pitcher baselines (later) via DuckDB `ASOF JOIN`.
- **Tests:** mirroring (LHP and RHP of identical shape produce identical features), label value tables fit only on train-season rows, PIT test (a baseline for date d contains no rows with date ≥ d), and a leakage test (shuffling labels gives R² ≈ 0).

### Future-proofing (no work now)
Partitioning by date already supports a daily incremental job (`ingest --date yesterday`). Paths go through a config `data_root`, so an S3/R2 URI works later via DuckDB httpfs. Your fantasy repo already has R2 upload code to reuse.

---

## 7. Repository structure

```
stuff-plus/
├── pyproject.toml            # uv, python>=3.14, console script `stuff`
├── uv.lock
├── README.md                 # metric definition + "not FanGraphs Stuff+" disclaimer
├── CLAUDE.md
├── configs/
│   ├── data.yaml             # seasons, data_root, rate limits
│   ├── labels/xrv_v1.yaml    # outcome classes, EV/LA bin sizes, fit seasons
│   ├── features/ff_v1.yaml   # allow-listed features + transforms
│   └── experiments/ff_lgbm_dev.yaml, ff_lgbm_final.yaml
├── src/stuffplus/
│   ├── cli.py                # typer app
│   ├── config.py             # pydantic models loading YAML
│   ├── ingest/{savant.py, manifest.py}
│   ├── silver/{build.py, quality.py, sql/*.sql}
│   ├── labels/run_values.py
│   ├── features/{physics.py, baselines.py, registry.py}
│   ├── datasets/{splits.py, build.py}
│   ├── models/{baselines.py, lgbm.py, tuning.py}
│   ├── evaluate/{pitch.py, pitcher.py, predictive.py, slices.py}
│   ├── scoring/{scale.py, aggregate.py}
│   └── explain/shap_report.py
├── notebooks/                # exploration only; import from src, never define logic here
│   ├── 01_eda_statcast.ipynb
│   ├── 02_labels.ipynb ...
├── tests/{fixtures/, test_ingest.py, test_silver.py, test_features.py, test_leakage.py, ...}
├── data/                     # gitignored: bronze/ silver/ gold/ scores/
└── mlruns/                   # gitignored; local MLflow file store
```

**CLI:**
```
uv run stuff ingest backfill --start 2022-04-01 --end 2026-10-01
uv run stuff silver build --season 2024
uv run stuff gold labels --config configs/labels/xrv_v1.yaml
uv run stuff gold features --config configs/features/ff_v1.yaml
uv run stuff train --config configs/experiments/ff_lgbm_dev.yaml
uv run stuff evaluate --run-id <id>
uv run stuff score --run-id <id> --season 2025
```

**Reproducibility:** uv.lock; every MLflow run logs the git SHA, dirty flag, config YAML, dataset hash, seeds, feature list, label-table artifact, model, and eval plots. Notebooks read gold or MLflow artifacts only. Configs are pydantic-validated YAML (Hydra would be overkill).

---

## 8. Roadmap (10 weeks, ~75 h)

### M1 — Small reproducible dataset + EDA (wk 1–2, ~12 h)
- **Objective:** trustworthy pitch data for a slice, and a real understanding of the columns.
- **Tasks:** repo scaffold; Savant fetcher + manifest; ingest **April 2024** only; silver build + DQ report; cross-check against pybaseball for 3 dates; EDA notebook covering FF distributions, the handedness sign check, null rates, `arm_angle`/`release_spin` availability, pitch_type mix. Then kick off the full 2022–2026 backfill in the background (~900 days at 1 req/s is under an hour plus retries).
- **ML concepts:** data generating process, unit of observation, what "noise" means for a pitch.
- **Deliverables:** bronze/silver for April 2024, DQ report, `01_eda`.
- **Acceptance:** rerunning ingest is a no-op; pitch_id unique; row counts match Savant within 0.5%; RHP FF median raw pfx_x < 0.
- **Risks:** Savant endpoint changes or throttling; column name drift between seasons.

### M2 — Label + baseline prediction problem (wk 3, ~8 h)
- **Tasks:** full-season silver; xRV_v1 value tables fit on 2022–23; label QA (distribution, outcome-class shares by season, 2025 vs 2026 called-strike shift); constant, velo-only and ridge baselines on dev splits; calibration-by-decile plot.
- **Concepts:** loss functions, bias/variance, why R² is tiny, regression to the mean.
- **Acceptance:** ridge beats constant on 2024 with a bootstrap CI excluding 0; velo coefficient has the right sign; label tables are reproducible from config.
- **Risks:** EV/LA bins too sparse (use coarser bins or smoothing).

### M3 — Gold features, splits, MLflow (wk 4, ~7 h)
- **Tasks:** ff_v1 features with mirroring + derived fields; split builder; dataset hashing; MLflow logging wrapper; leakage tests (allow-list, shuffled-label).
- **Concepts:** train/val/test semantics, leakage taxonomy (target, temporal, group).
- **Acceptance:** all tests green; the baselines re-run through MLflow match M2 numbers exactly.

### M4 — LightGBM v1 (wk 5, ~8 h)
- **Tasks:** default params + early stopping on 2024; learning curves; compare with baselines; write a short memo explaining each hyperparameter in your own words.
- **Concepts:** boosting internals (§5), early stopping, overfitting curves.
- **Acceptance:** LightGBM beats ridge on 2024 RMSE/R² (CI) and stays calibrated by decile.
- **Risks:** gains over ridge are tiny. That's still a legitimate result; document it.

### M5 — Tuning + memorization checks (wk 6, ~8 h)
- **Tasks:** GroupKFold-by-pitcher random search (15–30 configs); ablations (no release features, no arm angle, no spin); in-sample vs out-of-sample pitcher-score correlation gap; held-out-rookie subgroup.
- **Concepts:** grouped CV, hyperparameter search, the fingerprinting problem.
- **Acceptance:** chosen config justified by grouped CV, not 2024 alone; memorization gap quantified and acceptable.

### M6 — Scoring + reliability (wk 7, ~8 h)
- **Tasks:** scale.py with fixed reference; pitcher aggregation; leaderboards for 2024; split-half reliability; YoY; stabilization curve → set minimums; sanity-check the top and bottom 20 against your baseball intuition.
- **Concepts:** reliability vs validity, Spearman–Brown, shrinkage (optional: empirical-Bayes shrink low-N pitchers toward 100).
- **Acceptance:** split-half reliability at 250 FF reported; minimum N chosen from data.

### M7 — Predictive validity (dev) + external comparison (wk 8, ~9 h)
- **Tasks:** 2024 scores → 2025 outcomes; incremental regressions; sample-size strata; pull FanGraphs FF Stuff+ (`Stf+ FA`; pybaseball or a manual CSV export) and compare correlation and predictive power.
- **Concepts:** descriptive vs predictive vs incremental validity; confounding.
- **Acceptance:** a clear table of each predictor's r with next-season outcomes, including CIs.
- **Risks:** FanGraphs scraping blocked → manual export.

### M8 — Explainability, bias slices, freeze + final test (wk 9, ~8 h)
- **Tasks:** SHAP global plus dependence plots; slice table (hand, arm angle, role, park, month); write the final config and thresholds into git; **run the final protocol once** (train 2022–24 → 2025 scores → 2026 outcomes + 2026 pitch-level); write-up note in this vault.
- **Acceptance:** final results reported even if worse than dev. No re-tuning after seeing 2026.

### M9 — v2 decomposed classification model (wk 10, ~8 h, optional)
- **Tasks:** P(swing), P(whiff | swing), P(foul | contact), E[xRV_bip]; combine; compare to v1 on the identical harness. Note: the final 2026 test is already spent, so compare on dev, or accept that 2026 is now "seen".
- **Concepts:** log-loss, probability calibration (reliability diagrams, isotonic), model composition.

**Overall risks:** Savant access, 2026 schema shifts, sticky-stuff-era quirks, scope creep into secondaries before the FF model is validated, and over-reading tiny pitch-level metrics.

---

## 9. Methodological mistakes to avoid

1. Using `description`/`events`/`launch_*`/`delta_*`/`*_until_next_game` as features (direct leakage) → allow-list test.
2. Fitting outcome-value or EV/LA tables on all seasons (label leakage into test) → fit on train only.
3. Random pitch-level splits: the same pitcher's pitches end up in train and test, and the model gets credit for memorizing them → chronological + grouped.
4. Judging the model by same-season correlation with ERA or run value → it rewards memorization.
5. Bootstrapping CIs by pitch → understates uncertainty; resample games or pitchers.
6. Full-season pitcher means as features → mild look-ahead; use as-of baselines.
7. Tuning on the test season, or "just one more look" at 2026 → pre-register the final run.
8. Comparing location-dependent stats across 2025→2026 without the plate/ABS redefinition.
9. Ranking pitchers with tiny samples → minimums from the stabilization curve, show N.
10. Treating SHAP as causal ("adding 1 inch of IVB is worth X runs").
11. Claiming parity with FanGraphs Stuff+: you're building *a* Stuff-style metric with a documented definition.

---

## 10. Future extensions (not in MVP)

- **Multiple pitch types:** one model per family (FF/SI, FC, SL/ST/SV, CU/KC, CH/FS) or a single model with pitch type + **Δ vs primary fastball (PIT-correct)** features. Per-type reference scaling.
- **Arsenals:** usage-weighted pitcher Stuff+; interaction features (velo gap, movement separation, tunneling proxies from release consistency).
- **Detecting changes in pitch quality:** rolling, shrunk game-level Stuff with change-point detection (CUSUM/Bayesian), e.g. "velo +1.5 and ride +2 in since the last 3 starts".
- **Breakout prediction:** a second model on pitcher-season deltas (ΔStuff+, Δusage, new pitch, ΔK%, Δchase) → P(K–BB% improves by X next season). Needs many seasons of pairs, so it's a natural follow-on once 2022–2026 exist.
- **Fantasy:** export gold scores to `~/fantasy_baseball` (DuckDB can read the Parquet directly); waiver alerts on Stuff+ jumps among low-rostered pitchers.
- **App:** Streamlit/Evidence over DuckDB for leaderboards and pitch SHAP waterfalls; later a daily incremental job on GitHub Actions + R2, like your fantasy repo.

---

## Decisions

The four planning decisions were accepted on 2026-11-07 and are recorded as [ADRs 0001–0004](adr/); later ones are added there too.

## Endpoint probe findings (2026-11-07)

- `statcast_search/csv?all=true&type=details&player_type=pitcher&hfGT=R%7C&game_date_gt=D&game_date_lt=D` returns that single date (inclusive), regular season only.
- 119 columns; body starts with a UTF-8 BOM; fields quoted. ~2.9 MB / ~4,300 rows per full day; ~5.5 s per request.
- Off-day → header only (no rows), HTTP 200.
- Spin column is `release_spin_rate` (CSV docs say `release_spin`); `arm_angle`, `api_break_*`, bat-tracking columns present.
- Sanity: RHP FF median `pfx_x` = −0.55 ft (arm side negative, as expected); `(game_pk, at_bat_number, pitch_number)` unique.
- 2026 data available (2026-06-10: 4,573 rows, 119 cols).
- Bronze stores raw `.csv.gz` plus per-partition `_meta.json` markers ([ADR 0005](adr/0005-bronze-sidecar-completion-markers.md)).

## Sources

- [Statcast CSV docs](https://baseballsavant.mlb.com/csv-docs)
- [tjStuff+ v2.0 methodology](https://tjstats.ca/2024/05/04/modelling-tjstuff-plus-v20/)
- [aStuff+ v2](https://adamsalorio.substack.com/p/introducing-astuff-v2?open=false)
- [Beyond Whiffs (regression vs classification families)](https://sportsanalytics.studentorg.berkeley.edu/articles/beyond-whiffs.html)
- [Baseball Prospectus StuffPro/PitchPro](https://www.baseballprospectus.com/?p=89245)
- [MLB: ABS Challenge System for 2026](https://www.mlb.com/press-release/press-release-mlb-announces-abs-challenge-system-coming-to-the-major-leagues-beginning-in-the-2026-season)
- [pybaseball](https://github.com/jldbc/pybaseball)

## History (before GitHub Issues)

- **2026-11-07:** Repo scaffolded. Savant fetcher + bronze layer + `stuff ingest backfill|status` CLI, 26 tests (HTTP mocked). Live ingest of 2024-03-15 → 04-30: 47 dates, 36 game days, 133,403 pitches, 0 duplicate keys, 1 schema, 29 MB. Re-run is a no-op. Row and game counts match pybaseball exactly on 4 spot dates. pybaseball crashed once on a non-CSV Savant response, which validates the decision to write our own fetcher with retries.
- **2026-11-07:** Added CODING_STANDARDS.md (the code-review skill's source) and slimmed CLAUDE.md. Pushed to private GitHub repo `nkbelow/stuff-plus` (3 commits); GitHub Issues is the planned tracker for Matt Pocock's skills.
- **2026-11-07:** Ran setup-matt-pocock-skills: GitHub Issues tracker, default triage labels (4 created), single-context domain docs. Moved decisions into `docs/adr/0001`–`0005`; Obsidian `decisions.md` is now an index pointing there.
- **2026-11-07:** Moved plan, walkthrough and silver spec from Obsidian into `docs/`. From here on, progress is tracked in GitHub Issues.
