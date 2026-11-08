# Stuff+ model

Learning project: a custom Stuff+-style pitch quality model (four-seam fastballs first)
from pitch-level Statcast data and LightGBM. Learning applied ML is the goal, so explain
ML concepts in data-engineering terms with baseball examples while working.

**Plan and decisions live in the Obsidian vault**, not here:
`~/notes/stuff-plus/plan.md`, `decisions.md`, `walkthrough.md`. Read them before
starting a new milestone.

## Fixed decisions (see decisions.md)

- Seasons: 2022–23 train, 2024 tune/early-stop, 2025 dev "next season" outcomes,
  **2026 locked final test: never compute model metrics on it during development.**
- v1 target: denoised, count-neutral xRV regression. v2: decomposed classification.
- Own Savant fetcher with raw bytes preserved; pybaseball is only a cross-check.
- Features: physical only. No location, count, batter, park, or pitcher ID.
  Outcome/future columns (`description`, `events`, `delta_*`, `post_*`, `launch_*`,
  `estimated_*`, bat tracking, `*_days_until_next_game`) are forbidden as features.

## Layout

```
configs/data.yaml           data_root (env override STUFFPLUS_DATA_ROOT), season windows, Savant settings
src/stuffplus/
  config.py                 pydantic config models
  cli.py                    `stuff` typer app
  ingest/savant.py          single-date fetch: URL, retries, rate limit, CSV validation
  ingest/bronze.py          partition writes, backfill loop, manifest view
tests/                      pytest; HTTP mocked with httpx.MockTransport (no network)
data/                       gitignored: bronze/ silver/ gold/
```

Bronze: `data/bronze/statcast/season=YYYY/game_date=YYYY-MM-DD/{part.csv.gz,_meta.json}`.
`_meta.json` is written last and marks the partition complete.

## Commands

- `uv run stuff ingest backfill --season 2024` (or `--start/--end`; `--force` re-fetches)
- `uv run stuff ingest status`: per-season dates/pitches/duplicates/schema count
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`
- Cross-check against pybaseball (optional heavy group, not installed by default):
  `uv run --group crosscheck python ...`
- `STUFFPLUS_DATA_ROOT=/path` overrides `data_root` in `configs/data.yaml`.

## Statcast gotchas (verified 2026-11-07)

- Response body starts with a UTF-8 BOM; read with `utf-8-sig` (DuckDB handles it).
- 119 columns. Spin is `release_spin_rate` (CSV docs say `release_spin`).
- `pfx_x`/`pfx_z` are **feet**, catcher's perspective; RHP four-seamers have negative `pfx_x`.
- Off-days return a header-only CSV with HTTP 200.
- 2026: `plate_x/z` measured mid-plate (was front), `sz_top/bot` ABS-defined.
- Savant occasionally returns non-CSV bodies with HTTP 200; the fetcher retries those.
- DuckDB 2.x relations have no `fetchone()`; use `fetchall()`.

## Conventions

- Bronze is immutable raw data; transforms never mutate it.
- Silver = cleaned facts only (no feature logic). Gold = labels/features/datasets.
- Value tables used for labels are fit on training seasons only.
- Keep infra local and simple: no Airflow, Docker, or cloud until there's a real need.
