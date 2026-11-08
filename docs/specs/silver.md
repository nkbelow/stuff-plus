# Spec: Silver Layer

Milestone 1, part 2. Parent: [`plan.md`](../plan.md) §6 · decisions: [`adr/`](../adr/).
This is the spec `/code-review` checks silver work against. Written 2026-11-07. Tasks are tracked as GitHub Issues.

---

## Goal

One clean, typed row per regular-season pitch, `silver.pitches`, rebuilt reproducibly from
bronze. Plus a data quality report that tells us, before any modeling, where the data is
incomplete or odd. Silver contains **facts only**: it means exactly what Savant's CSV docs
say, with correct types and a stable key. Every modeling choice (filters, units, mirroring,
outcome classes) waits for gold.

## What profiling bronze showed (Mar 15 – Apr 30 2024, 133,403 rows)

| Finding | Implication for silver |
|---|---|
| DuckDB's type inference on one file read `spin_axis`, `launch_angle`, `babip_value` as integers | **Explicit schema**, not inference: a decimal on some later date would break or silently change types |
| 0 duplicate `(game_pk, at_bat_number, pitch_number)`; one `game_type` (R) | Key is sound; dedup is a safety net, plus an assertion |
| `automatic_ball` (429) / `automatic_strike` (30): **pitch-clock violations**, mostly with no physics or pitch type | Keep in silver (they're real events in the count); gold excludes them from modeling |
| 3 `hit_into_play` rows have `type = 'S'` with `events = catcher_interf` | Consistency checks need a known-exceptions list |
| Missing spin (1.25%) is **concentrated in whole games** (COL 746570: 300 pitches, TB 745116: 291, MIN 745922: 288) | Tracking outages are per game. DQ reports coverage **per game**, not only per month |
| Velocity null 0.33%, nearly all automatic calls (7 rows otherwise) | Missing velocity ≈ "no pitch thrown" |
| `arm_angle` null 1.55%; `pitch_type` null 0.35% | Report only; gold decides |
| Position players pitching (`FA`/`EP`, ~57–68 mph: Mervis, Miller, Rortvedt…) | Keep in silver; flag for EDA. Gold's FF filter mostly excludes them anyway |
| 7 always-null deprecated columns (`spin_dir`, `spin_rate_deprecated`, `break_*_deprecated`, `tfs_*`, `umpire`) | Drop in silver (assert they're null first) |

## Design

### Table: `silver.pitches`

- **Storage:** `data/silver/pitches/season=YYYY/part.parquet`, one file per season (~750k rows). A season is the rebuild unit: DuckDB rebuilds one in seconds, so there's no need for 186 small daily files.
- **Key:** `pitch_id = game_pk || '-' || at_bat_number || '-' || pitch_number` (VARCHAR), unique across the season. Dedup runs across the **whole season**, not per day: a suspended game resumed on a later date can put one `game_pk` in two daily partitions.
- **Columns:** every non-deprecated Savant column (~112), with **Savant's names and units unchanged** (`pfx_x` stays in feet; [ADR 0006](../adr/0006-silver-keeps-savant-names-units-and-columns.md)). Units are recorded in the schema contract, not converted. Conversions and unit-suffixed names happen in gold (standards §1.7–1.8).
- **Lineage:** `bronze_fetched_at` (joined from `_meta.json`), so every silver row traces back to the download it came from.
- **No filters except an assertion:** `game_type = 'R'` is asserted, not filtered (bronze is already regular-season only, so a violation means something upstream broke). Automatic calls, position-player pitches and rows missing tracking all stay; filtering is gold's job.
- **Sort order:** `game_date, game_pk, at_bat_number, pitch_number`, so Parquet output is deterministic (standards §3.2).

### Schema contract: `src/stuffplus/silver/contract.py`

The single source of truth for silver columns. Each entry holds name, DuckDB type, unit, group (`id`, `context`, `physics`, `location`, `outcome`, `savant_model`, `bat_tracking`), and the first season it is expected to be populated (Savant sends all 119 columns every season; seasons before a column existed send it empty, e.g. bat tracking in 2022). The build generates its `SELECT` cast list from the contract, so the column list isn't written twice. Gold's forbidden-column test can later reuse the groups.

- Measurements → `DOUBLE`, even ones that look like integers today (spin, axis, angles).
- IDs and counts → `BIGINT` / `INTEGER`; codes → `VARCHAR`; `game_date` → `DATE`.
- `CAST` is strict, not `TRY_CAST`. A value that can't be cast fails the build and names the column and date (standards §4.1).

### Write–audit–publish

The same pattern Iceberg/Delta teams call **WAP**:

1. **Write** the season to a staging path (`data/silver/_staging/…`).
2. **Audit:** run the data quality checks against the staged file.
3. **Publish** with an atomic rename, only if no hard check failed. If one did, the previously published season stays untouched, and the failing report is still written for inspection.

This is how a bad Savant day never reaches the model while it can still be investigated.

### Data quality checks: `src/stuffplus/silver/quality.py`

Each check is a SQL query returning violations, plus a severity.

**Hard (block publish):**
- `pitch_id` unique after collapsing exact duplicates. Conflicting duplicates (same key, different values) fail.
- Every row has `game_type = 'R'`, and `game_date` matches its bronze partition.
- Identifiers not null: `game_pk`, `at_bat_number`, `pitch_number`, `pitcher`, `batter`, `game_date`.
- `description` is in the known vocabulary (the 15 values seen). A new value fails, because gold's outcome-class mapping must be updated deliberately.
- **Schema contract:** each bronze file's header matches the schema contract. A missing expected column fails; a new, unknown column warns and is listed.
- Deprecated columns are entirely null before they're dropped.
- Row count: silver = bronze − exact duplicates.

**Warn (reported, don't block):**
- Null rate of core physics (`release_speed`, `release_spin_rate`, `spin_axis`, `pfx_x/z`, `release_pos_x/z`, `release_extension`, `arm_angle`) per **season × month** and per **game**. Games under 50% spin coverage are listed as tracking outages.
- Ranges: velocity 30–106 mph, spin 0–4,000 rpm, extension 3–9 ft, release height 0–8 ft, `|pfx|` ≤ 3 ft.
- Pitches per game outside 150–450 (rain-shortened and extra-inning games are legitimate; the list is for eyeballing).
- `description` ↔ `type` consistency, with known exceptions (catcher interference).
- `hit_into_play` without `launch_speed`; `pitch_type` null outside automatic calls.

**Outputs** (Parquet under `data/silver/dq/`, also exposed as views):
- `dq_results`: check_name, season, severity, passed, metric, threshold, detail, audited_at (`check` is a SQL reserved word; a boolean `passed` is clearer than a status string; `audited_at` makes each audit's file unique by design)
- `dq_null_rates`: season, month, column, null %
- `dq_games`: game_pk, date, home team, pitches, spin coverage, flags

The CLI prints a summary and exits non-zero if any hard check fails.

### Catalog: `data/stuff.duckdb`

A DuckDB database file holding only schemas and views, no data; it can be rebuilt any time. Views: `bronze.statcast`, `bronze.manifest`, `silver.pitches`, `silver.dq_results`, `silver.dq_null_rates`, `silver.dq_games`.

- Refreshed automatically after each silver build, and on demand with `stuff catalog refresh`.
- Paths are written from `DataConfig`, so `STUFFPLUS_DATA_ROOT` still works.
- Query it with the DuckDB CLI (`brew install duckdb`, then `duckdb data/stuff.duckdb`), the DuckDB UI (`duckdb -ui`), notebooks, or DBeaver.

### CLI

```
uv run stuff silver build [--season 2024 ...]   # default: every season present in bronze
uv run stuff silver dq    [--season 2024 ...]   # re-audit published silver, print the report
uv run stuff catalog refresh
```

### Module layout

```
src/stuffplus/silver/
  __init__.py
  contract.py    schema contract: one entry per silver column (single source of truth)
  build.py       build_season(): bronze → staged parquet → audit → publish
  quality.py     check definitions + report writers
  sql/pitches.sql  select/dedup template; cast list generated from schema.py
src/stuffplus/catalog.py   create/refresh data/stuff.duckdb views
tests/test_silver_build.py, test_silver_quality.py, test_catalog.py
```

## Tasks

Tracked as sub-issues of [#1](https://github.com/nkbelow/stuff-plus/issues/1), cut as vertical slices by `/to-tickets` with native blocking links. The full 2022–2026 backfill is [#2](https://github.com/nkbelow/stuff-plus/issues/2). Estimated total ≈ 8 h. Next after silver: the EDA notebook (walkthrough §3).

## Acceptance criteria

- `stuff silver build` publishes every season with zero hard failures. Re-running produces identical content: the same rows and the same hash of the sorted data. (Byte-identical files aren't required: DuckDB writes its version into Parquet metadata, and thread count can change row-group layout.)
- Silver row count per season = bronze rows − exact duplicates, reconciled in `dq_results`.
- Known findings show up where expected: the three April 2024 spin-outage games appear in `dq_games`; automatic calls are counted; catcher interference is the only description/type exception.
- All tests pass offline; ruff passes.

## ML concepts this milestone teaches

- **Schema-on-read vs schema-on-write:** bronze is read with no schema; silver commits to one. Why "let the library guess types" is a reproducibility bug.
- **Data contracts:** the schema contract plus hard checks are the agreement between Savant and every downstream model (the same idea as dbt model contracts or Delta schema enforcement).
- **Missingness mechanisms:** spin missing for *whole games at specific parks* is not random noise. Dropping those rows later removes certain park-days, which matters if park correlates with outcomes (Coors). EDA will check whether the outages cluster.
- **Write–audit–publish:** why validation sits between writing and publishing, not after.

## Risks and unknowns

- Columns that only became populated in later seasons (bat tracking; possibly others in 2023, 2025, 2026). The schema contract records the first expected season, and a warn check flags a column that is empty when it should be populated. Confirmed on [#3](https://github.com/nkbelow/stuff-plus/issues/3) for 2022 and 2024.
- Savant may reclassify pitch types between our download and a later re-download. `bronze_fetched_at` lineage makes that diagnosable.
- Full-season files are ~100–150 MB of Parquet each (estimate). Fine locally; worth confirming.
