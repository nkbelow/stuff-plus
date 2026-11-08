"""Silver data quality checks and their results table.

Each check is one SQL query over a staged silver season, producing a `CheckResult`.
Hard checks block publishing; warn checks are reported only. Results are written for
every audit, passing or failing, so a blocked publish can still be investigated.

The tracer (#10) carries one hard check; #12 and #13 add the rest.
"""

import datetime as dt
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import duckdb

from stuffplus.sql import quote_literal


class Severity(StrEnum):
    HARD = "hard"
    WARN = "warn"


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    season: int
    severity: Severity
    passed: bool
    metric: float
    threshold: float
    detail: str


def run_audit(con: duckdb.DuckDBPyConnection, pitches: Path, season: int) -> list[CheckResult]:
    """Run every check against a staged silver season."""
    return [_pitch_id_unique(con, pitches, season)]


def _pitch_id_unique(con: duckdb.DuckDBPyConnection, pitches: Path, season: int) -> CheckResult:
    dupes, examples = con.execute(
        """
        WITH counts AS (
            SELECT pitch_id, count(*) AS n FROM read_parquet(?) GROUP BY pitch_id HAVING n > 1
        )
        SELECT
            (SELECT coalesce(sum(n - 1), 0) FROM counts),
            (SELECT string_agg(pitch_id, ', ')
             FROM (SELECT pitch_id FROM counts ORDER BY pitch_id LIMIT 5))
        """,
        [str(pitches)],
    ).fetchall()[0]
    return CheckResult(
        check_name="pitch_id_unique",
        season=season,
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    tmp = path.with_name(f".{path.name}.tmp")
    con.execute(f"COPY _dq_results TO {quote_literal(tmp)} (FORMAT parquet)")
    os.replace(tmp, path)
