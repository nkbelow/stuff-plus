"""Build one silver season from bronze using write–audit–publish.

  1. Write: type, deduplicate and sort the season into the staging area.
  2. Audit: run the data quality checks on the staged file and write their results,
     pass or fail.
  3. Publish: atomically rename the staged file into place, only if no hard check
     failed. On failure the previously published season stays untouched, and the
     staged file is kept for inspection.

File locations are defined once, in `layout.py`.

Failure semantics:

  - No complete, non-empty bronze partitions for the season -> SilverBuildError.
  - A value that won't cast to its contract type -> SilverBuildError naming the season
    (DuckDB's message names the value and target type).
  - A hard check fails -> SilverAuditError (a SilverBuildError) carrying the failures.
  - `build_seasons` records each season's SilverBuildError and carries on.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from stuffplus.config import DataConfig
from stuffplus.ingest.bronze import Partition, complete_partitions
from stuffplus.silver.contract import KEY_COLUMNS, SILVER_COLUMNS, SORT_COLUMNS, SOURCE_COLUMNS
from stuffplus.silver.layout import SilverLayout
from stuffplus.silver.quality import (
    CheckResult,
    Severity,
    StagedSeason,
    run_audit,
    write_results,
)
from stuffplus.sql import quote_literal

SQL_TEMPLATE = Path(__file__).parent / "sql" / "pitches.sql"


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


@dataclass
class BuildSummary:
    built: list[BuildResult] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


def build_season(cfg: DataConfig, season: int) -> BuildResult:
    # Off-days are header-only files: they add no rows, and leaving them out keeps
    # DuckDB's CSV sniffing on files that have data.
    partitions = [p for p in complete_partitions(cfg.bronze_dir, season) if p.rows > 0]
    if not partitions:
        raise SilverBuildError(f"season {season}: no complete bronze partitions with rows")

    layout = SilverLayout(cfg.silver_dir)
    staged_path = layout.staging(season)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute("CREATE TEMP TABLE lineage (data_file VARCHAR, fetched_at TIMESTAMPTZ)")
        con.executemany(
            "INSERT INTO lineage VALUES (?, ?)",
            [[str(p.data_file), p.fetched_at] for p in partitions],
        )
        try:
            con.execute(
                f"COPY ({render_sql(partitions)}) TO {quote_literal(staged_path)} (FORMAT parquet)"
            )
        except duckdb.ConversionException as exc:
            raise SilverBuildError(f"season {season}: a value doesn't fit its type: {exc}") from exc

        results = run_audit(StagedSeason(con, staged_path, season))
        write_results(con, results, layout.dq_results(season))
        failed = [r for r in results if r.severity is Severity.HARD and not r.passed]
        if failed:
            raise SilverAuditError(season, failed)

        published = layout.pitches(season)
        published.parent.mkdir(parents=True, exist_ok=True)
        staged_path.replace(published)
        (rows,) = con.execute("SELECT count(*) FROM read_parquet(?)", [str(published)]).fetchall()[
            0
        ]
        return BuildResult(season=season, rows=rows, results=results)


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
        print(f"{season}: published {result.rows:,} pitches")
    return summary


def render_sql(partitions: list[Partition]) -> str:
    def quoted(names: Iterable[str]) -> str:
        return ", ".join(f'"{name}"' for name in names)

    return SQL_TEMPLATE.read_text().format(
        casts=",\n        ".join(
            f'CAST(raw."{c.name}" AS {c.type}) AS "{c.name}"' for c in SOURCE_COLUMNS
        ),
        source_columns=quoted(c.name for c in SOURCE_COLUMNS),
        pitch_id=" || '-' || ".join(f'"{name}"' for name in KEY_COLUMNS),
        sort=quoted(SORT_COLUMNS),
        output_columns=quoted(c.name for c in SILVER_COLUMNS),
        data_files="[" + ", ".join(quote_literal(p.data_file) for p in partitions) + "]",
    )
