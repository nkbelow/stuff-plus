# Coding Standards

How code in this repo is written. Read it before writing or reviewing code. Formatting,
import order, pyupgrade, bugbear and simplify rules are enforced by ruff (`pyproject.toml`)
and are not repeated here.

**Section 1 rules are hard: breaking one invalidates model results.** Sections 2–6 are
strong defaults; a deliberate deviation carries a comment saying why.

## 1. Data correctness

The model is only as honest as its inputs. Every rule here guards against **leakage**:
information about the outcome, or about the future, reaching training or evaluation.

1. **Allow-listed features.** Model inputs come only from the feature list in
   `configs/features/*.yaml`. Stuff features are physical pitch traits; location
   (`plate_*`, `zone`, `sz_*`), situation (count, base/out, inning, score, batter, park,
   umpire, catcher) and identity (`pitcher`, `player_name`, team) stay out.
2. **Outcome and future columns are never features:** `description`, `type`, `events`,
   `des`, `bb_type`, `launch_*`, `hit_*`, `hc_*`, `estimated_*`, `woba_*`, `babip_value`,
   `iso_value`, `delta_*`, `post_*`, `*_win_exp`, bat tracking (`bat_speed`,
   `swing_length`, `attack_*`, `swing_path_tilt`, `intercept_*`), and
   `*_days_until_next_game`. This list is the single source of truth; the allow-list
   test enforces it.
3. **Fit on training seasons only.** Anything learned from data (label value tables,
   EV/LA buckets, scalers, encoders, imputation statistics, score μ/σ) is fit on the
   training seasons, saved as an artifact, and loaded downstream, never refit on
   evaluation data.
4. **Chronological splits.** Train/validation/test split by season. Cross-validation
   inside the training seasons groups by pitcher (`GroupKFold`).
5. **2026 is locked.** Development evaluation uses 2024 (pitch level) and 2024 scores →
   2025 outcomes (pitcher level). Only the final experiment config computes metrics on
   2026.
6. **Point-in-time baselines.** Pitcher-relative features use only games strictly before
   the pitch's `game_date` (DuckDB `ASOF JOIN` with a strict `<`).
7. **One mirroring function.** Handedness normalization (arm side positive) lives in one
   gold function. Mirrored columns carry an `_arm` marker (`hb_arm_in`); raw columns keep
   Savant's names.
8. **Savant columns keep Savant's names and units through silver**; units are recorded in
   the silver schema contract. Columns derived in gold carry their unit in the name:
   `_in`, `_ft`, `_mph`, `_rpm`, `_deg` (`pfx_x` is feet; `hb_arm_in` is inches).
9. **Labels are never imputed.** Rows missing a core physical feature are dropped and
   counted in a data quality report.
10. **Bootstrap by game or pitcher**, never by pitch. Pitches within a game are correlated.

## 2. Layers and module boundaries

1. **Bronze** is raw response bytes, written only by `ingest/`. Nothing modifies it.
2. **Silver** is typed, cleaned facts: an explicit column list in outputs (no `SELECT *`),
   explicit casts, and no modeling choices (no mirroring, no derived features).
3. **Gold** holds labels, features and training datasets as separate tables keyed by
   `pitch_id`.
4. **Imports flow one way:** `ingest → silver → gold → models → evaluate / scoring`. A
   module imports only from its own layer or earlier ones; `config` is shared by all.
5. **`cli.py` is thin.** Each command parses arguments, loads config, calls one library
   function, and reports the result. Logic lives in the layer modules.
6. **Notebooks explore; `src/` decides.** Notebooks import from `stuffplus`; logic used
   twice, or used to produce a reported number, moves into `src/` with a test.
7. **Build for the current milestone.** Generalize on the second real use; infrastructure
   stays local-first (files + DuckDB + local MLflow).

## 3. I/O and reproducibility

1. **Atomic writes:** write to a temp path, then `os.replace`. When a partition has a
   completion marker, the marker is written last.
2. **Idempotent re-runs:** same inputs give the same outputs. Gzip with `mtime=0`, sort
   rows before writing Parquet, and fix every random seed.
3. **Paths come from `DataConfig`** (`data_root`, overridable via `STUFFPLUS_DATA_ROOT`).
4. **Tunables live in YAML**, validated by pydantic models in `config.py`. Module
   constants hold fixed facts about external systems, each with a comment on its source
   (e.g. `ROW_CAP`).
5. **Every training run logs to MLflow:** the resolved config, git SHA + dirty flag,
   dataset content hash, seeds, and the feature list.

## 4. Errors and data checks

1. **Fail loudly on broken expectations.** Error messages name the unit (date,
   partition, column) and the expected vs actual value.
2. **Classify failures:** transient errors (network, 429/5xx, non-CSV body) retry with
   backoff; permanent errors fail fast. `ingest/savant.py` is the pattern.
3. **Batch loops isolate failures per unit:** record the failure, continue with the next
   date or season, and exit non-zero at the end listing what failed.
4. **Catch specific exceptions.** A broad `except Exception` belongs only at a CLI
   boundary that then exits non-zero.
5. **Data expectations are code:** a check that matters (uniqueness, ranges, row
   counts) runs in the pipeline or a test, not only in a notebook.

## 5. Tests

1. **Tests run offline and fast:** HTTP through `httpx.MockTransport`, files under
   `tmp_path`, time and sleep injected. Tests never read `data/`.
2. **Each transform has a hand-built fixture** with its expected output.
3. **Leakage guardrails are tests:** the feature allow-list, train-only fitting,
   point-in-time baselines, and a shuffled-label check (R² ≈ 0).
4. **A bug fix ships with a regression test** that fails without the fix.
5. **Tests call library functions.** CLI tests cover argument wiring only.

## 6. Code beyond ruff

1. Type hints on every public function signature. (Python 3.14 evaluates annotations lazily,
   so `from __future__ import annotations` isn't needed.)
2. Modules that do I/O open with a docstring stating their purpose and failure semantics.
3. Comments explain *why*: the baseball, Savant, or statistical reason. The code
   already says *what*.
4. Values passed between functions with more than three fields are a frozen dataclass
   (e.g. `FetchResult`) rather than a tuple or dict.
5. Pipeline progress goes to stdout via `print`, one line per unit of work.
6. DuckDB SQL uses uppercase keywords and one CTE per logical step. A query longer than
   ~15 lines lives in a `.sql` file beside its module.
7. **Dependencies arrive with the milestone that uses them.** Heavy optional tools go in a
   non-default dependency group; a dependency that has broken us gets a major-version cap.
