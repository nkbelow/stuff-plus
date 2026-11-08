"""Silver data quality checks and their results table.

Each check is one SQL query over a staged silver season, producing a `CheckResult`.
Hard checks block publishing (#12 adds more; #13 adds warn checks, which report only).
Results are written for every audit, passing or failing, so a blocked publish can still
be investigated.

Failure semantics:

  - A check that fails is a result (`passed=False`), not an exception.
  - A check whose query can't run (bad SQL, unreadable staged file) raises duckdb.Error.
  - `write_results` replaces a season's results file atomically, or leaves the old one.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import duckdb

from stuffplus.fs import atomic_path
from stuffplus.sql import quote_literal


class Severity(StrEnum):
    HARD = "hard"


@dataclass(frozen=True)
class StagedSeason:
    """A season written to staging and awaiting its audit."""

    con: duckdb.DuckDBPyConnection
    path: Path
    season: int


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    season: int
    severity: Severity
    passed: bool
    metric: float
    threshold: float
    detail: str


def run_audit(staged: StagedSeason) -> list[CheckResult]:
    """Run every check against a staged silver season."""
    return [_pitch_id_unique(staged)]


def _pitch_id_unique(staged: StagedSeason) -> CheckResult:
    dupes, examples = staged.con.execute(
        """
        WITH counts AS (
            SELECT pitch_id, count(*) AS n FROM read_parquet(?) GROUP BY pitch_id HAVING n > 1
        )
        SELECT
            (SELECT coalesce(sum(n - 1), 0) FROM counts),
            (SELECT string_agg(pitch_id, ', ')
             FROM (SELECT pitch_id FROM counts ORDER BY pitch_id LIMIT 5))
        """,
        [str(staged.path)],
    ).fetchall()[0]
    return CheckResult(
        check_name="pitch_id_unique",
        season=staged.season,
        severity=Severity.HARD,
        passed=dupes == 0,
        metric=float(dupes),
        threshold=0.0,
        detail=(
            "every pitch_id is unique"
            if dupes == 0
            else f"{dupes} rows share a pitch_id with different values, e.g. {examples}"
        ),
    )


def write_results(con: duckdb.DuckDBPyConnection, results: list[CheckResult], path: Path) -> None:
    """Write one season's audit results atomically."""
    # audited_at records when the audit ran, so results files differ between runs by
    # design. Idempotency (CODING_STANDARDS §3.2) applies to the data, not its audit log.
    audited_at = dt.datetime.now(dt.UTC)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE _dq_results (
            check_name VARCHAR, season INTEGER, severity VARCHAR, passed BOOLEAN,
            metric DOUBLE, threshold DOUBLE, detail VARCHAR, audited_at TIMESTAMPTZ
        )
        """
    )
    con.executemany(
        "INSERT INTO _dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                r.check_name,
                r.season,
                r.severity,
                r.passed,
                r.metric,
                r.threshold,
                r.detail,
                audited_at,
            ]
            for r in results
        ],
    )
    with atomic_path(path) as tmp:
        con.execute(f"COPY _dq_results TO {quote_literal(tmp)} (FORMAT parquet)")
