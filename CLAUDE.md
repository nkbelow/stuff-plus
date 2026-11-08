# stuff-plus

A custom Stuff+-style pitch quality model (four-seam fastballs first) built from Statcast
data with LightGBM. Nick's goal is learning applied ML, so work as a **mentor**: when a new
ML concept enters the work (a loss function, leakage, early stopping, SHAP), explain it in
data-engineering terms with a baseball example alongside the code.

## Where things live

- **Design and roadmap:** `docs/plan.md`. Step-by-step learning explainer:
  `docs/walkthrough.md`. When starting or scoping a milestone, read its open GitHub Issues
  and `docs/adr/`.
- **Progress:** GitHub Issues. When a task finishes, close its issue with a comment that
  records the results (key numbers, surprises, follow-ups).
- **What a Statcast column means** (baseball terms, real examples): `docs/data-guide.md`.
- **Coding standards:** `CODING_STANDARDS.md`. Read it before writing or reviewing code.
  It is the single source for the leakage rules and forbidden feature columns.
- **Spec for `/code-review`:** the milestone's parent GitHub issue. Silver predates this:
  its spec is `docs/specs/silver.md`, linked from #1.
- **Commands:** `uv run stuff --help`.

## Workflow

Each milestone runs the skills' main flow: `/grill-with-docs` → `/to-spec` (parent issue)
→ `/to-tickets` (sub-issues with blocking links) → `/implement #N` per ticket → `/pr` →
`/retro`. Milestone work lives on one branch named for the milestone (`silver`, `labels`);
each commit references its ticket (`Closes #N`), which is how `/code-review` finds the spec.

## Guardrails

- **2026 is locked.** Evaluate on 2024 (pitch level) and 2024 scores → 2025 outcomes
  (pitcher level). 2026 metrics come only from the final experiment config, run once.
- **Savant is a free public service, and each request takes ~5 s.** Agree backfills
  longer than a month with Nick before running them.
- **Done** means `uv run pytest`, `uv run ruff check .` and `uv run mypy` pass (mypy is strict
  for `src/`; tests may leave helpers unannotated).

## Gotchas (verified 2026-11-07)

- Savant CSVs start with a UTF-8 BOM (DuckDB handles it; Python needs `utf-8-sig`).
  They have 119 columns, and spin is `release_spin_rate` (the CSV docs say `release_spin`).
- `pfx_x`/`pfx_z` are **feet**, catcher's perspective: RHP four-seamers have negative `pfx_x`.
- Off-days return a header-only CSV with HTTP 200. Savant sometimes returns a non-CSV
  body with HTTP 200; the fetcher retries it.
- From 2026, `plate_x/z` are measured mid-plate (front of plate before), and `sz_top/bot`
  are ABS-defined. Location is not comparable across 2025→2026.
- DuckDB 2.x relations have no `fetchone()`; use `fetchall()`.
- All 1,030 bronze partitions share one 119-column header (verified 2022–2026). Reading every
  bronze file with `union_by_name = true` exhausted 12.7 GB of memory at bind time; use it only
  per season.
- DuckDB needs `pytz` to return `TIMESTAMPTZ` values (e.g. `bronze_fetched_at`) to Python.
- DuckDB auto-detects hive partitions in `key=value` paths: reading bronze without
  `hive_partitioning = false` replaces the file's `game_date` with the directory's.
- DuckDB's `CAST('1.5' AS INTEGER)` rounds to 2. Silver's casts are strict by checking an
  integer pattern first (`silver/build.py`). DuckDB 2 lambdas are `lambda x: ...`, not `x -> ...`.
- pybaseball lives in the optional `crosscheck` group: `uv run --group crosscheck python ...`.
- `STUFFPLUS_DATA_ROOT` overrides `data_root` in `configs/data.yaml`.

## Silver data findings (2022–2026, #14)

- Spin tracking outages are whole games, clustered by park and run of days (CIN Aug 11–14
  2022, STL Jun 2023, HOU Jul 2024, TEX and DET 2025): 2–10 games a season, listed in
  `silver.dq_games`. Dropping them later removes specific park-days, not random pitches.
- `description`/`type` disagree on catcher interference (excused, 6–13 a season) and on
  0–2 plays a season scored in play (`type = X`, `events = field_out`) with a pitch-call
  description, mostly batter interference. Those warn; they are not excused.
- `api_break_*` are feet, like `pfx_*`. Bat tracking starts 2023-07-14. `hyper_speed` is an
  outcome (adjusted exit velocity); `miss_distance` is bat tracking.
- 2026 brought one new `description`, `swinging_pitchout`; a new value blocks publishing
  until it is added to `contract.DESCRIPTION_TYPES` and gold's outcome mapping.

## Agent skills

### Issue tracker

GitHub Issues on nkbelow/stuff-plus, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical names, unchanged. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
