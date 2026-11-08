"""Build one silver season from bronze using write–audit–publish, or re-audit one.

  1. Write: type, deduplicate and sort the season into the staging area.
  2. Audit: run the data quality checks on the staged file and write their results and
     reports, pass or fail.
  3. Publish: atomically rename the staged file into place, only if no hard check
     failed. On failure the previously published season stays untouched, and the
     staged file is kept for inspection.

`audit_season` runs step 2 alone against a published season, rewriting its results and
reports without touching the data. File locations are defined once, in `layout.py`.

Failure semantics:

  - No complete, non-empty bronze partitions for the season, or an unreadable bronze
    file -> SilverBuildError.
  - A value that won't cast to its contract type -> SilverBuildError naming the season,
    column, value and partition date. Integers are strict too: DuckDB's own cast would
    round "1.5" to 2.
  - A hard check fails -> SilverAuditError (a SilverBuildError) carrying the failures.
  - Re-auditing a season that isn't published -> SilverBuildError.
  - `build_seasons` and `audit_seasons` record each season's SilverBuildError and carry on.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from stuffplus.config import DataConfig
from stuffplus.silver.contract import (
    KEY_COLUMNS,
    SILVER_COLUMNS,
    SORT_COLUMNS,
    SOURCE_COLUMNS,
    Column,
)
from stuffplus.silver.layout import SilverLayout
from stuffplus.silver.quality import (
    AuditContext,
    CheckResult,
    run_audit,
    write_audit,
)
from stuffplus.silver.source import ROW_HASH, SQL_DIR, BronzeSeason, profile_bronze
from stuffplus.sql import quote_literal

INTEGER_TYPES = {"INTEGER", "BIGINT"}


class SilverBuildError(Exception):
    """A season can't be built."""


class SilverAuditError(SilverBuildError):
    """The staged season failed a hard check; nothing was published."""

    def __init__(self, season: int, failed: list[CheckResult]) -> None:
        self.season = season
        self.failed = failed
        reasons = "; ".join(f"{r.check_name}: {r.detail}" for r in failed)
        super().__init__(f"season {season} not published: {reasons}")


@dataclass(frozen=True)
class BuildResult:
    season: int
    rows: int
    results: list[CheckResult]

    @property
    def warnings(self) -> int:
        return sum(r.warns for r in self.results)


@dataclass
class BuildSummary:
    built: list[BuildResult] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


@dataclass
class AuditSummary:
    audited: dict[int, list[CheckResult]] = field(default_factory=dict)
    failed: dict[int, str] = field(default_factory=dict)  # season -> why it couldn't run

    @property
    def blocked(self) -> list[int]:
        """Seasons with a hard failure, or whose audit couldn't run."""
        hard = [s for s, results in self.audited.items() if any(r.blocks for r in results)]
        return sorted([*hard, *self.failed])


def build_season(cfg: DataConfig, season: int) -> BuildResult:
    bronze = _load_bronze(cfg, season)
    layout = SilverLayout(cfg.silver_dir)
    staged_path = layout.staging(season)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        bronze.register(con)
        try:
            con.execute(
                f"COPY ({render_sql(bronze)}) TO {quote_literal(staged_path)} (FORMAT parquet)"
            )
        except duckdb.InvalidInputException as exc:
            # Raised by a strict cast's error() (or an unparseable CSV): the message
            # names the column, value and partition date.
            raise SilverBuildError(f"season {season}: {exc}") from exc

        results = _audit(con, cfg, bronze, staged_path)
        failed = [r for r in results if r.blocks]
        if failed:
            raise SilverAuditError(season, failed)

        published = layout.pitches(season)
        published.parent.mkdir(parents=True, exist_ok=True)
        staged_path.replace(published)
        (rows,) = con.execute("SELECT count(*) FROM read_parquet(?)", [str(published)]).fetchall()[
            0
        ]
        return BuildResult(season=season, rows=rows, results=results)


def audit_season(cfg: DataConfig, season: int) -> list[CheckResult]:
    """Re-audit a published season against current bronze, without rebuilding it."""
    published = SilverLayout(cfg.silver_dir).pitches(season)
    if not published.exists():
        raise SilverBuildError(f"season {season}: not published, nothing to audit")
    bronze = _load_bronze(cfg, season)
    with duckdb.connect() as con:
        bronze.register(con)
        return _audit(con, cfg, bronze, published)


def build_seasons(cfg: DataConfig, seasons: Iterable[int]) -> BuildSummary:
    """Build each season in turn. One season failing doesn't stop the rest."""
    summary = BuildSummary()
    for season in seasons:
        try:
            result = build_season(cfg, season)
        except SilverBuildError as exc:
            print(f"[error] {exc}")
            summary.failed[season] = str(exc)
            continue
        summary.built.append(result)
        print(f"{season}: published {result.rows:,} pitches ({result.warnings} warnings)")
    return summary


def audit_seasons(cfg: DataConfig, seasons: Iterable[int] | None = None) -> AuditSummary:
    """Re-audit each season in turn (default: every published one). One season failing
    doesn't stop the rest."""
    summary = AuditSummary()
    if seasons is None:
        seasons = SilverLayout(cfg.silver_dir).published_seasons()
    for season in seasons:
        try:
            summary.audited[season] = audit_season(cfg, season)
        except SilverBuildError as exc:
            print(f"[error] {exc}")
            summary.failed[season] = str(exc)
    return summary


def render_sql(bronze: BronzeSeason) -> str:
    def quoted(names: Iterable[str]) -> str:
        return ", ".join(f'"{name}"' for name in names)

    present = bronze.columns
    return (
        (SQL_DIR / "pitches.sql")
        .read_text()
        .format(
            casts=",\n        ".join(_cast(c, c.name in present) for c in SOURCE_COLUMNS),
            row_hash=ROW_HASH,
            source=bronze.relation(),
            source_columns=quoted(c.name for c in SOURCE_COLUMNS),
            pitch_id=" || '-' || ".join(f'"{name}"' for name in KEY_COLUMNS),
            sort=quoted(SORT_COLUMNS),
            output_columns=quoted(c.name for c in SILVER_COLUMNS),
        )
    )


def _cast(column: Column, present: bool) -> str:
    """A strict cast of one bronze column to its contract type."""
    name, kind = column.name, column.type
    if not present:
        # The schema contract check fails the audit; the build still runs so every other
        # check reports too.
        return f'CAST(NULL AS {kind}) AS "{name}"'
    raw = f'raw."{name}"'
    if kind == "VARCHAR":
        return f'{raw} AS "{name}"'
    fits = f"TRY_CAST({raw} AS {kind}) IS NOT NULL"
    if kind in INTEGER_TYPES:
        fits += f" AND regexp_full_match({raw}, '-?[0-9]+')"
    message = f"'{name} = ''' || {raw} || ''' on ' || lineage.game_date || ': not a {kind}'"
    return (
        f"CASE WHEN {raw} IS NULL THEN NULL WHEN {fits} THEN TRY_CAST({raw} AS {kind}) "
        f'ELSE error({message}) END AS "{name}"'
    )


def _load_bronze(cfg: DataConfig, season: int) -> BronzeSeason:
    try:
        bronze = BronzeSeason.load(cfg.bronze_dir, season)
    except (OSError, EOFError, ValueError) as exc:
        raise SilverBuildError(f"season {season}: unreadable bronze file: {exc}") from exc
    if not bronze.partitions:
        raise SilverBuildError(f"season {season}: no complete bronze partitions with rows")
    return bronze


def _audit(
    con: duckdb.DuckDBPyConnection, cfg: DataConfig, bronze: BronzeSeason, path: Path
) -> list[CheckResult]:
    ctx = AuditContext(
        con=con,
        path=path,
        season=bronze.season,
        bronze=bronze,
        profile=profile_bronze(con, bronze),
        quality=cfg.quality,
    )
    results = run_audit(ctx)
    write_audit(con, results, SilverLayout(cfg.silver_dir), bronze.season)
    return results
