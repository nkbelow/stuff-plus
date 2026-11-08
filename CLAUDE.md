# stuff-plus

A custom Stuff+-style pitch quality model (four-seam fastballs first) built from Statcast
data with LightGBM. Nick's goal is learning applied ML, so work as a **mentor**: when a new
ML concept enters the work (a loss function, leakage, early stopping, SHAP), explain it in
data-engineering terms with a baseball example alongside the code.

## Where things live

- **Plan, decisions, progress log:** `~/notes/stuff-plus/` (`plan.md`, `decisions.md`,
  `walkthrough.md`). When starting or scoping a milestone, read `plan.md`'s progress log
  and `decisions.md`. When a milestone task finishes, append a dated line to the progress
  log.
- **Coding standards:** `CODING_STANDARDS.md`. Read it before writing or reviewing code.
  It is the single source for the leakage rules and forbidden feature columns.
- **Spec for `/code-review`:** the milestone's spec note in `~/notes/stuff-plus/`
  (`silver-plan.md` for silver); otherwise its section of `plan.md`.
- **Commands:** `uv run stuff --help`.

## Guardrails

- **2026 is locked.** Evaluate on 2024 (pitch level) and 2024 scores → 2025 outcomes
  (pitcher level). 2026 metrics come only from the final experiment config, run once.
- **Savant is a free public service, and each request takes ~5 s.** Agree backfills
  longer than a month with Nick before running them.
- **Done** means `uv run pytest` and `uv run ruff check .` pass.

## Gotchas (verified 2026-11-07)

- Savant CSVs start with a UTF-8 BOM (DuckDB handles it; Python needs `utf-8-sig`).
  They have 119 columns, and spin is `release_spin_rate` (the CSV docs say `release_spin`).
- `pfx_x`/`pfx_z` are **feet**, catcher's perspective: RHP four-seamers have negative `pfx_x`.
- Off-days return a header-only CSV with HTTP 200. Savant sometimes returns a non-CSV
  body with HTTP 200; the fetcher retries it.
- From 2026, `plate_x/z` are measured mid-plate (front of plate before), and `sz_top/bot`
  are ABS-defined. Location is not comparable across 2025→2026.
- DuckDB 2.x relations have no `fetchone()`; use `fetchall()`.
- pybaseball lives in the optional `crosscheck` group: `uv run --group crosscheck python ...`.
- `STUFFPLUS_DATA_ROOT` overrides `data_root` in `configs/data.yaml`.
