# Stuff+ — Step-by-Step Walkthrough

Companion to [`plan.md`](plan.md) and the [ADRs](adr/). Numbers marked ~ are estimates to be measured in M1.

Flow: **raw pitches → cleaned pitches → label + features per pitch → model → pitcher scores → evidence the scores mean something.**

---

## 1. Bronze — download raw data (wk 1–2)

- One Savant request per game date (cap ~25k rows; a day is ~4.5k). 2022–2026 ≈ 1,000 dates, ~3.6M pitches, ~5 s/request.
- Saved as `data/bronze/statcast/season=YYYY/game_date=YYYY-MM-DD/part.csv.gz` (exact bytes) + `_meta.json` (rows, sha256, column hash, fetched_at, status).
- Why raw: Savant reprocesses history (pitch-type relabels, tracking fixes); raw lets you tell "source changed" from "my code changed". Column hash flags schema drift.
- Idempotent: temp file → atomic rename; meta written last = "complete"; backfill skips completed dates unless `--force`; off-days get a 0-row meta.

## 2. Silver — clean pitches

- DuckDB SQL: typed columns, `game_type='R'`, `pitch_id = game_pk-at_bat_number-pitch_number` (unique, tested), dedupe on latest fetch, pfx feet→inches.
- **Facts only.** No mirroring or features — those are modeling choices and live in gold.
- DQ report per season × month: null rates, range checks (velo 55–106, spin 500–3800, ext 4–8.5), pitches/game ~250–350, unknown pitch types. Per-month so park/tracking outages don't hide in season averages.

## 3. EDA notebook — each question feeds a decision

1. FF distributions by hand; confirm RHP FF raw `pfx_x` < 0 before mirroring.
2. Column availability/naming across seasons (`arm_angle`, `release_spin_rate`).
3. Season-level physics shifts (2022→2023 jump?).
4. Pitch-type fuzziness (FF < 88 mph, cutter-like FF, pitchers whose FF jumped).
5. 2025 vs 2026 called-strike rate by zone — **labels only, never model metrics.**
6. Volume: FF per season, pitchers with ≥100 / ≥250 FF.

## 4. Label — xRV (wk 3) ⭐

Problem: one pitch's actual result is mostly noise plus things the pitcher doesn't control (runners, outs, defense, park). Fix: replace the actual result with **the average value of that kind of result**, fit on 2022–23 only.

| Savant `description` | Class |
|---|---|
| ball, blocked_ball | ball |
| called_strike | called strike |
| swinging_strike, swinging_strike_blocked, foul_tip | whiff |
| foul | foul |
| hit_by_pitch | HBP |
| hit_into_play | ball in play |
| foul_bunt, missed_bunt, bunt_foul_tip, pitchout, intentional balls | **drop** |

- Non-contact: mean `delta_run_exp` per class (illustrative: ball +0.06, called strike −0.06, whiff −0.10, foul −0.04, HBP +0.3). Count-neutral (a whiff is worth the same in 0-0 as in 0-2) — a simplification to revisit.
- Ball in play: mean `delta_run_exp` per exit-velocity × launch-angle bucket (~3 mph × 4°, sparse buckets merged). 105 mph @ 28° ≈ +1.0; 85 mph @ −10° ≈ −0.2. Removes defense/luck and most of the shift-ban effect.
- Example: 2-1 foul → −0.04. 104 mph lineout @ 15° → ≈ +0.6 (hard contact gets "blamed").
- Why train-only: the value tables are part of the label definition; fitting on test seasons leaks.
- Regression on these labels learns Σ P(outcome | physics) × value(outcome) — the same quantity the v2 classifier builds piece by piece.

## 5. Features (wk 4)

- YAML allow-list + a test that fails on any outcome or future column.
- Mirroring (arm side positive): `hb = -pfx_x` (RHP) / `pfx_x` (LHP); same for `release_pos_x`; `spin_axis → 360 − axis` for LHP. Test: same shape from either hand → identical rows.
- v1: velocity, IVB (ride), HB (run), spin rate, spin axis, release height & side, extension, arm angle, movement magnitude/angle.
- Out: location (command), count/runners/batter/park (situation), pitcher ID (identity).

## 6. Splits & baselines (wk 3–4)

- Chronological, not random (random puts Cole's April in train and his May in test → rewards recognizing Cole).
- Baselines: constant → velocity-only → ridge. Expect R² ~0.01 per pitch; the signal appears in averages.
- CIs: resample **games**, not pitches.

## 7. LightGBM (wk 5)

1. Start: predict the mean for every pitch.
2. Residual = actual − predicted.
3. Tree fit to residuals: features bucketed into ~255 bins, residuals summed per bin, scan for the best split (≈ GROUP BY bin + scan). Tree 1 likely: velo ≥ 95, then IVB ≥ 17.
4. Add 0.05 × tree (learning rate). 5. Repeat; later trees find subtler pockets.
6. Early stopping on 2024: stop ~100 trees after the validation best. Train-vs-val curve = first look at overfitting.
- Knobs: `num_leaves`, **`min_data_in_leaf` high (hundreds–thousands)**, `lambda_l2`, `feature_fraction`.
- Calibration: 10 groups by predicted xRV, predicted vs actual mean → diagonal.

## 8. Tuning + memorization (wk 6)

- 15–30 random configs, **GroupKFold by pitcher** (a pitcher never on both sides).
- Fingerprinting: release height + side + extension + arm angle + velo ≈ unique → a leaf can become "Chris Sale" and learn his results (command, luck).
- Tests: (1) drop release features — accuracy flat but rankings shift = identification; (2) 2023 vs 2024 correlation with results — big gap = memorization; (3) rookies unseen in 2022–23.

## 9. Scores (wk 7)

- `Stuff = 100 + 10 × (μ − pred) / σ` with μ, σ from all 2024 FF predictions (fixed reference). E.g. μ −0.005, σ 0.012, pred −0.020 → **112.5**. Higher = better.
- Pitcher = mean of pitch scores, always with N. Fixed reference → league velo growth shows as an average > 100 (real information).
- Split-half (odd vs even games), `r_full = 2r/(1+r)`; stabilization curve → minimum N at ~0.7 reliability (expect low hundreds).
- Gut check top/bottom 20.

## 10. Predicts the future? (wk 8)

- 2024 scores → 2025 FF whiff%, FF RV/100, K%, K−BB%, ERA estimator (not ERA). ~300–400 pitchers.
- Compare: our Stuff+, actual 2024 FF RV, whiff%, velo alone, FanGraphs FF Stuff+.
- **Incremental test:** `K%_2025 ~ actual_FF_RV_2024 + Stuff_2024`. Stuff must add value beyond results.
- By sample size: Stuff should win big at ~120 pitches and converge by ~1,500.

## 11. Explain + bias (wk 9)

- SHAP: global importance, IVB × release-height interaction (flat fastball), single-pitcher breakdowns. Explains the model, not baseball; correlated features split credit arbitrarily.
- Bias slices: hand, arm angle, starter vs reliever (velo/context confound), month, park.

## 12. Final test (once)

Commit config + thresholds → retrain 2022–24 → score 2025 → vs 2026 results and 2026 pitch-level → report whatever happens, no re-tuning.

## 13. v2 classification (wk 10, optional)

P(swing), P(whiff|swing), P(foul|contact), E[value|BIP] → combine → compare with v1. Expect whiff|swing to be very predictable from shape, swing/called strike barely (location-driven) — a direct demonstration of why Stuff and Location are separate metrics.
