# Data guide: what Baseball Savant sends

What the Statcast pitch-level export contains, in baseball terms, with real examples from this
repo's bronze data. Numbers are from the 2024 regular season (computed 2026-11-07).

- **Types and units** of silver columns: the schema contract, `src/stuffplus/silver/contract.py`
  (single source of truth).
- **Ingestion gotchas** (file encoding, empty off-days, 2026 coordinate change): `CLAUDE.md`.
- **Official column definitions:** [Statcast CSV docs](https://baseballsavant.mlb.com/csv-docs).

## One row = one pitch

About 715,000 rows per regular season, 119 columns each, identical header in every season from
2022 to 2026. Columns that didn't exist yet (bat tracking before 2024) arrive empty, not missing.

A pitch is identified by `game_pk` + `at_bat_number` (plate appearance within the game) +
`pitch_number` (pitch within the plate appearance). Silver joins them into `pitch_id`.

## A plate appearance, pitch by pitch

Kevin Gausman (TOR, right-handed) vs. a right-handed Rockies hitter, 1st inning, 2024-04-12.
HB and IVB are `pfx_x` and `pfx_z` converted from feet to inches.

```
 #  count type   mph   rpm  HB in IVB in  plate_x plate_z  description      event      run value
 1   0-0   SL   83.6  2179    1.7    5.8    0.85    2.31   called_strike               -0.039
 2   0-1   SL   84.5  2247    2.2    7.8    1.05    0.77   ball                         0.026
 3   1-1   FS   85.7  1784  -14.5    5.0    0.28    1.31   swinging_strike             -0.066
 4   1-2   FS   86.4  1849  -16.9    3.2    1.01    0.09   ball                         0.039
 5   2-2   FF   94.8  2285   -8.4   19.0   -0.03    3.92   swinging_strike  strikeout  -0.223
```

Sliders early, his low-spin splitter to set up, then an elevated four-seamer with 19 inches of
induced vertical break that the hitter swings under. The count is the count *before* each pitch.

That last pitch shows the central modeling question: how much of its value is the **pitch**
(velocity, ride, release) and how much is **location and count** (high in the zone, two strikes)?
Stuff+ tries to credit only the first. See [ADR 0002](adr/0002-denoised-xrv-regression-target.md)
and the plan's discussion of integrating out location.

## Column groups

| Group | Examples | Meaning | Role in this project |
|---|---|---|---|
| Identity | `game_pk`, `at_bat_number`, `pitch_number`, `pitcher`, `batter`, `player_name` | Who threw what, when | Keys and pitcher aggregation; never a feature |
| Game state | `balls`, `strikes`, `outs_when_up`, `on_1b/2b/3b`, `inning`, scores | Situation before the pitch | Context; excluded from Stuff features |
| Release | `release_speed`, `release_spin_rate`, `spin_axis`, `release_pos_x/z`, `release_extension`, `arm_angle` | How the ball left the hand | The core of Stuff+ |
| Movement | `pfx_x`, `pfx_z` (feet), `api_break_*` (inches) | Movement relative to a spinless ball | Core features, converted to inches in gold |
| Trajectory | `vx0/vy0/vz0`, `ax/ay/az` | Physics fit of the flight at 50 ft from the plate | Redundant with movement; source for derived angles |
| Location | `plate_x`, `plate_z`, `zone`, `sz_top/bot` | Where the pitch crossed the plate | Excluded from Stuff (that's command); redefined in 2026 |
| Outcome | `description`, `type` (B/S/X), `events` | Result of the pitch / ball-strike-in play / result of the plate appearance | Builds the xRV label in gold; forbidden as a feature |
| Batted ball | `launch_speed`, `launch_angle`, `hit_distance_sc`, `bb_type` | Contact quality on balls in play | Exit-velocity × launch-angle buckets for the label |
| Savant's models | `pitch_type`, `pitch_name`, `estimated_woba_using_speedangle`, `estimated_ba_…` | Savant's classifications and estimates | `pitch_type` selects pitch families; estimates are outcomes |
| Run and win value | `delta_run_exp`, `delta_pitcher_run_exp`, `home_win_exp`, … | Change in expected runs or win chance from the pitch | The raw, noisy quantity the label denoises |
| Bat tracking | `bat_speed`, `swing_length`, `attack_angle`, … | The hitter's swing | Empty before 2024; forbidden as features |
| Workload | `n_thruorder_pitcher`, `pitcher_days_since_prev_game`, `pitcher_days_until_next_game` | Fatigue and schedule | The "until next game" columns look into the future: a leakage trap |
| Deprecated | `spin_dir`, `umpire`, `tfs_deprecated`, … | Old tracking system | Always empty; dropped in silver |

**Coordinates:** `x` is horizontal from the **catcher's view**. Gausman's splitter reads −14.5"
because it runs toward his arm side (third-base side, from behind the plate); a left-hander's
mirror-image pitch reads +14.5". Gold mirrors left-handers so arm side has one sign.

## Pitch shapes (2024, right-handed pitchers)

```
type name             share   mph   rpm    HB    IVB  ext ft
FF   4-Seam Fastball  31.8%  94.7  2309  -7.5  15.8   6.54
SL   Slider           15.7%  86.2  2442   4.5   1.7   6.44
SI   Sinker           15.3%  93.6  2156 -14.8   7.2   6.46
CH   Changeup          9.0%  86.1  1827 -14.2   4.9   6.47
FC   Cutter            8.7%  90.0  2421   2.6   8.3   6.43
ST   Sweeper           7.9%  82.5  2585  13.9   1.3   6.44
CU   Curveball         6.0%  79.7  2629   9.8 -10.0   6.41
FS   Split-Finger      3.9%  86.7  1329 -10.7   2.6   6.51
```

Four-seamers have the most ride; sinkers trade ride for arm-side run; sweepers move most to the
glove side; curveballs drop; splitters work by killing spin. Physics alone separates the pitch
types, which is why it carries real signal about pitch quality.

## Pitch outcomes (2024)

```
ball                     235,786  33.1%   avg run value +0.055
foul                     128,707  18.1%   avg run value -0.035
hit_into_play            124,207  17.4%   avg run value +0.051
called_strike            116,208  16.3%   avg run value -0.063
swinging_strike           74,769  10.5%   avg run value -0.107
blocked_ball              15,023   2.1%   avg run value +0.055
foul_tip                   7,352   1.0%   avg run value -0.117
swinging_strike_blocked    3,935   0.6%   avg run value -0.157
automatic_ball             2,249          avg run value n/a
hit_by_pitch               2,020          avg run value +0.346
foul_bunt                  1,233          avg run value -0.048
missed_bunt                  202          avg run value -0.046
automatic_strike             138          avg run value n/a
pitchout                      54          avg run value +0.032
bunt_foul_tip                 16          avg run value -0.045
```

Run value is from the batting team's side: positive is good for the hitter.

- These averages are the raw material for the xRV label's outcome-class values.
- A whiff is worth nearly twice a called strike: whiffs happen more often with two strikes,
  where they end the plate appearance.
- **Automatic calls** (pitch-clock violations, no pitch thrown) carry **no run value** in
  Savant's data. Gold excludes them rather than weighting them.

## Why raw run value is noisy

Two 2024 home runs with similar contact:

```
home_run  EV 109.8 mph  LA 30°  434 ft   xwOBA 2.00   run value +1.543
home_run  EV 113.3 mph  LA 25°  387 ft   xwOBA 2.00   run value +2.636
```

The second is worth a run more only because more runners were on base: the situation differed,
not the pitch. Savant's own contact model (`estimated_woba_using_speedangle`, about 2.0 for a home
run on the wOBA scale) rates them the same. That's why the label replaces raw `delta_run_exp`
for balls in play with the average for its exit-velocity × launch-angle bucket.
