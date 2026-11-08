"""Build one silver season from bronze using write–audit–publish.

  1. Write: type, deduplicate and sort the season into a staging file.
  2. Audit: run the data quality checks on the staged file and write their results,
     pass or fail.
  3. Publish: atomically rename the staged file into place, only if no hard check
     failed. On failure the previously published season stays untouched, and the
     staged file is kept for inspection.

Layout under `silver_dir`:

  pitches/season=YYYY/part.parquet              published
  _staging/pitches/season=YYYY/part.parquet     last build, before publishing
  dq/dq_results/season=YYYY/part.parquet        last audit's results

Failure semantics:

  - No complete, non-empty bronze partitions for the season -> SilverBuildError.
  - A value that won't cast to its contract type -> duckdb's ConversionException.
  - A hard check fails -> SilverAuditError carrying the failed results.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from stuffplus.config import DataConfig
from stuffplus.ingest.bronze import Partition, complete_partitions
from stuffplus.silver.contract import SILVER_COLUMNS
from stuffplus.silver.quality import CheckResult, Severity, run_audit, write_results
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


def pitches_path(silver_dir: Path, season: int) -> Path:
    return silver_dir / "pitches" / f"season={season}" / "part.parquet"


def staging_path(silver_dir: Path, season: int) -> Path:
    return silver_dir / "_staging" / "pitches" / f"season={season}" / "part.parquet"


def dq_results_path(silver_dir: Path, season: int) -> Path:
    return silver_dir / "dq" / "dq_results" / f"season={season}" / "part.parquet"


def build_season(cfg: DataConfig, season: int) -> BuildResult:
    # Off-days are header-only files: they add no rows, and leaving them out keeps
    # DuckDB's CSV sniffing on files that have data.
    partitions = [p for p in complete_partitions(cfg.bronze_dir, season) if p.rows > 0]
    if not partitions:
        raise SilverBuildError(f"season {season}: no complete bronze partitions with rows")

    staged = staging_path(cfg.silver_dir, season)
    staged.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute("CREATE TEMP TABLE lineage (data_file VARCHAR, fetched_at TIMESTAMPTZ)")
        con.executemany(
            "INSERT INTO lineage VALUES (?, ?)",
            [[str(p.data_file), p.fetched_at] for p in partitions],
        )
        con.execute(f"COPY ({render_sql(partitions)}) TO {quote_literal(staged)} (FORMAT parquet)")

        results = run_audit(con, staged, season)
        write_results(con, results, dq_results_path(cfg.silver_dir, season))
        failed = [r for r in results if r.severity is Severity.HARD and not r.passed]
        if failed:
            raise SilverAuditError(season, failed)

        published = pitches_path(cfg.silver_dir, season)
        published.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, published)
        rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(published)])
        return BuildResult(season=season, rows=rows.fetchall()[0][0], results=results)


@dataclass
class BuildSummary:
    built: list[BuildResult] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


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
    casts = ",\n        ".join(
        f'CAST(raw."{c.name}" AS {c.type}) AS "{c.name}"' for c in SILVER_COLUMNS
    )
    data_files = "[" + ", ".join(quote_literal(p.data_file) for p in partitions) + "]"
    return SQL_TEMPLATE.read_text().format(casts=casts, data_files=data_files, lineage="lineage")
