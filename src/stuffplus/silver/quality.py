"""Silver data quality checks and reports.

Each check produces one `CheckResult`. Hard checks block publishing; warn checks only
report (thresholds in `QualityConfig`). Most checks are a SQL query over the audited
season, exposed as the temp view `audited`; the rest read the bronze profile. Two
reports go with the results: null rates of core physics by month (`dq_null_rates`) and
per-game coverage (`dq_games`), since tracking outages happen a whole game at a time.

Everything is written on every audit, passing or failing, so a blocked publish can still
be investigated.

Failure semantics:

  - A check that fails is a result (`passed=False`), not an exception.
  - A check whose query can't run (bad SQL, unreadable file) raises duckdb.Error.
  - `write_audit` replaces each output file atomically, or leaves the old one.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import duckdb

from stuffplus.config import QualityConfig
from stuffplus.fs import atomic_path
from stuffplus.silver.contract import (
    AUTOMATIC_CALLS,
    DEPRECATED_COLUMNS,
    DESCRIPTION_TYPES,
    IDENTIFIER_COLUMNS,
    SOURCE_COLUMNS,
)
from stuffplus.silver.layout import SilverLayout
from stuffplus.silver.source import SQL_DIR, BronzeProfile, BronzeSeason
from stuffplus.sql import quote_literal

# The pitch traits Stuff+ is built from (plan.md); their missingness is reported by month.
CORE_PHYSICS = (
    "release_speed",
    "release_spin_rate",
    "spin_axis",
    "pfx_x",
    "pfx_z",
    "release_pos_x",
    "release_pos_z",
    "release_extension",
    "arm_angle",
)

# Rows whose `type` legitimately disagrees with their `description`. On catcher
# interference the batter reaches first; Savant records `hit_into_play` with type S.
KNOWN_TYPE_EXCEPTIONS = {"catcher_interf": "catcher interference"}

EXAMPLES = 5  # violations quoted in a result's detail
GAME_EXAMPLES = 20  # flagged games quoted in a result's detail


class Severity(StrEnum):
    HARD = "hard"  # blocks publishing
    WARN = "warn"  # reported only


@dataclass(frozen=True)
class AuditContext:
    """One season being audited: a staged file before publishing, or a published one."""

    con: duckdb.DuckDBPyConnection  # with the season's bronze registered
    path: Path
    season: int
    bronze: BronzeSeason
    profile: BronzeProfile
    quality: QualityConfig


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    season: int
    severity: Severity
    passed: bool
    metric: float
    threshold: float
    detail: str


def run_audit(ctx: AuditContext) -> list[CheckResult]:
    """Build the reports, then run every check against the audited season."""
    ctx.con.execute(
        "CREATE OR REPLACE TEMP VIEW audited AS SELECT * FROM "
        f"read_parquet({quote_literal(ctx.path)}, hive_partitioning = false)"
    )
    _build_reports(ctx)
    return [
        # Hard
        _schema_contract(ctx),
        _deprecated_columns_empty(ctx),
        _row_reconciliation(ctx),
        _pitch_id_unique(ctx),
        _game_date_matches_partition(ctx),
        _row_check(
            ctx,
            "game_type_regular",
            Severity.HARD,
            where="game_type IS DISTINCT FROM 'R'",
            example="coalesce(game_type, 'NULL')",
            ok="every row is a regular-season game",
            bad="rows from other game types",
        ),
        _row_check(
            ctx,
            "identifiers_not_null",
            Severity.HARD,
            where=" OR ".join(f'"{c}" IS NULL' for c in IDENTIFIER_COLUMNS),
            example="concat_ws(', ', "
            + ", ".join(f"CASE WHEN \"{c}\" IS NULL THEN '{c}' END" for c in IDENTIFIER_COLUMNS)
            + ")",
            ok="no identifier is null",
            bad="rows with a null identifier",
        ),
        _row_check(
            ctx,
            "description_known",
            Severity.HARD,
            where=f"description IS NULL OR description NOT IN {_sql_list(DESCRIPTION_TYPES)}",
            example="coalesce(description, 'NULL')",
            ok=f"every description is one of the {len(DESCRIPTION_TYPES)} known values",
            bad="rows with a description outside the vocabulary (extend gold's outcome "
            "mapping, then contract.DESCRIPTION_TYPES)",
        ),
        # Warn
        _unknown_columns(ctx),
        _columns_populated(ctx),
        *(
            _row_check(
                ctx,
                f"range_{column}",
                Severity.WARN,
                where=f'"{column}" NOT BETWEEN {bounds.min} AND {bounds.max}',
                example=f"pitch_id || ' = ' || \"{column}\"",
                ok=f"{column} within {bounds.min:g}–{bounds.max:g}",
                bad=f"rows with {column} outside {bounds.min:g}–{bounds.max:g}",
            )
            for column, bounds in ctx.quality.ranges.items()
        ),
        _game_check(
            ctx,
            "spin_outage_games",
            flag="spin_outage",
            ok="no game is a spin tracking outage",
            bad=f"games with spin on under {ctx.quality.spin_outage_coverage:.0%} of pitches "
            "thrown (tracking outages)",
        ),
        _game_check(
            ctx,
            "pitches_per_game",
            flag="pitch_count",
            ok="every game's pitch count is in range",
            bad=f"games outside {ctx.quality.pitches_per_game.min:g}–"
            f"{ctx.quality.pitches_per_game.max:g} pitches (rain, extra innings?)",
        ),
        _description_type_consistent(ctx),
        _row_check(
            ctx,
            "bip_launch_speed",
            Severity.WARN,
            where="description = 'hit_into_play' AND type = 'X' AND launch_speed IS NULL",
            example="pitch_id",
            ok="every ball in play has an exit velocity",
            bad="balls in play without launch_speed",
        ),
        _row_check(
            ctx,
            "pitch_type_missing",
            Severity.WARN,
            where=f"pitch_type IS NULL AND description NOT IN {_sql_list(AUTOMATIC_CALLS)}",
            example="pitch_id",
            ok="every pitch thrown has a pitch_type",
            bad="pitches thrown without a pitch_type (automatic calls excluded)",
        ),
    ]


def has_hard_failure(results: list[CheckResult]) -> bool:
    return any(r.severity is Severity.HARD and not r.passed for r in results)


def format_report(season: int, results: list[CheckResult]) -> str:
    """One line per check: failures and warnings first."""
    failed = [r for r in results if r.severity is Severity.HARD and not r.passed]
    warned = [r for r in results if r.severity is Severity.WARN and not r.passed]
    lines = [
        f"{season}: {len(results) - len(failed) - len(warned)} passed, "
        f"{len(failed)} hard failure(s), {len(warned)} warning(s)"
    ]
    for r in sorted(results, key=lambda r: (r.passed, r.severity is Severity.WARN)):
        status = "pass" if r.passed else "FAIL" if r.severity is Severity.HARD else "WARN"
        lines.append(f"  {status}  [{r.severity}] {r.check_name:28} {r.detail}")
    return "\n".join(lines)


def write_audit(
    con: duckdb.DuckDBPyConnection, results: list[CheckResult], layout: SilverLayout, season: int
) -> None:
    """Write one season's results and reports, each atomically."""
    # audited_at records when the audit ran, so results files differ between runs by
    # design. Idempotency (CODING_STANDARDS §3.2) applies to the data, not its audit log.
    audited_at = dt.datetime.now(dt.UTC)
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE dq_results (
            check_name VARCHAR, season INTEGER, severity VARCHAR, passed BOOLEAN,
            metric DOUBLE, threshold DOUBLE, detail VARCHAR, audited_at TIMESTAMPTZ
        )
        """
    )
    con.executemany(
        "INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [r.check_name, r.season, r.severity, r.passed, r.metric, r.threshold, r.detail]
            + [audited_at]
            for r in results
        ],
    )
    for report in ("dq_results", "dq_null_rates", "dq_games"):
        with atomic_path(layout.dq(report, season)) as tmp:
            con.execute(f"COPY {report} TO {quote_literal(tmp)} (FORMAT parquet)")


def _build_reports(ctx: AuditContext) -> None:
    thrown = f"description NOT IN {_sql_list(AUTOMATIC_CALLS)}"
    per_column = " UNION ALL ".join(
        f"SELECT month(game_date) AS month, '{c}' AS column_name, count(*) AS pitches_thrown, "
        f'100.0 * count(*) FILTER (WHERE "{c}" IS NULL) / count(*) AS null_pct '
        f"FROM audited WHERE {thrown} GROUP BY month"
        for c in CORE_PHYSICS
    )
    ctx.con.execute(
        f"CREATE OR REPLACE TEMP TABLE dq_null_rates AS SELECT {ctx.season} AS season, * "
        f"FROM ({per_column}) ORDER BY month, column_name"
    )
    games = ctx.quality.pitches_per_game
    ctx.con.execute(
        (SQL_DIR / "games.sql").read_text().format(season=ctx.season, thrown=thrown),
        [ctx.quality.spin_outage_coverage, games.min, games.max],
    )


def _result(
    ctx: AuditContext, name: str, severity: Severity, metric: float, detail: str
) -> CheckResult:
    return CheckResult(
        check_name=name,
        season=ctx.season,
        severity=severity,
        passed=metric == 0,
        metric=float(metric),
        threshold=0.0,
        detail=detail,
    )


def _row_check(
    ctx: AuditContext,
    name: str,
    severity: Severity,
    *,
    where: str,
    example: str,
    ok: str,
    bad: str,
) -> CheckResult:
    """Count the audited rows matching `where`, quoting a few distinct `example` values."""
    count, examples = ctx.con.execute(
        f"""
        SELECT count(*), (
            SELECT string_agg(e, ', ') FROM (
                SELECT DISTINCT {example} AS e FROM audited WHERE {where} ORDER BY e
                LIMIT {EXAMPLES}
            )
        )
        FROM audited WHERE {where}
        """
    ).fetchall()[0]
    detail = ok if count == 0 else f"{count:,} {bad}, e.g. {examples}"
    return _result(ctx, name, severity, count, detail)


def _game_check(ctx: AuditContext, name: str, *, flag: str, ok: str, bad: str) -> CheckResult:
    count, examples = ctx.con.execute(
        f"""
        SELECT count(*), string_agg(
            game_pk || ' (' || home_team || ' ' || game_date || ', ' || pitches || ' pitches, '
                || round(100 * spin_coverage)::INTEGER || '% spin)',
            ', ' ORDER BY game_date, game_pk
        ) FILTER (WHERE rank <= {GAME_EXAMPLES})
        FROM (
            SELECT *, row_number() OVER (ORDER BY game_date, game_pk) AS rank
            FROM dq_games WHERE list_contains(flags, ?)
        )
        """,
        [flag],
    ).fetchall()[0]
    more = f" (first {GAME_EXAMPLES})" if count > GAME_EXAMPLES else ""
    detail = ok if count == 0 else f"{count:,} {bad}{more}: {examples}"
    return _result(ctx, name, Severity.WARN, count, detail)


def _schema_contract(ctx: AuditContext) -> CheckResult:
    """Every bronze file carries every contract column. Missing ones are cast as NULL."""
    missing: dict[str, list[dt.date]] = {}
    for p in ctx.bronze.partitions:
        header = set(ctx.bronze.headers[p.data_file])
        for column in SOURCE_COLUMNS:
            if column.name not in header:
                missing.setdefault(column.name, []).append(p.game_date)
    metric = sum(len(dates) for dates in missing.values())
    detail = (
        f"every bronze file has all {len(SOURCE_COLUMNS)} contract columns"
        if not missing
        else "missing from bronze: "
        + "; ".join(f"{c} ({len(d)} files from {min(d)})" for c, d in sorted(missing.items()))
    )
    return _result(ctx, "schema_contract", Severity.HARD, metric, detail)


def _unknown_columns(ctx: AuditContext) -> CheckResult:
    """Columns Savant sends that the contract doesn't know. Silver leaves them out."""
    known = {c.name for c in SOURCE_COLUMNS} | set(DEPRECATED_COLUMNS)
    extra = sorted(ctx.bronze.columns - known)
    detail = (
        "no columns outside the schema contract"
        if not extra
        else f"new bronze columns, not in silver (add them to the contract): {', '.join(extra)}"
    )
    return _result(ctx, "schema_unknown_columns", Severity.WARN, len(extra), detail)


def _deprecated_columns_empty(ctx: AuditContext) -> CheckResult:
    values = {c: n for c, n in ctx.profile.deprecated_values.items() if n}
    detail = (
        "deprecated columns are empty, so dropping them loses nothing"
        if not values
        else "deprecated columns now have values (keep them in the contract?): "
        + ", ".join(f"{c} ({n:,} rows)" for c, n in values.items())
    )
    return _result(ctx, "deprecated_columns_empty", Severity.HARD, sum(values.values()), detail)


def _row_reconciliation(ctx: AuditContext) -> CheckResult:
    """Silver rows = bronze rows − exact duplicates, with bronze counted at fetch time."""
    (silver_rows,) = ctx.con.execute("SELECT count(*) FROM audited").fetchall()[0]
    p = ctx.profile
    expected = p.rows - p.exact_duplicates
    sign = "=" if silver_rows == expected else "≠"
    detail = (
        f"silver {silver_rows:,} {sign} bronze {p.rows:,} − {p.exact_duplicates:,} exact "
        f"duplicates ({p.rows_read:,} bronze rows read)"
    )
    return _result(ctx, "row_reconciliation", Severity.HARD, abs(silver_rows - expected), detail)


def _pitch_id_unique(ctx: AuditContext) -> CheckResult:
    dupes, examples = ctx.con.execute(
        """
        WITH counts AS (
            SELECT pitch_id, count(*) AS n FROM audited GROUP BY pitch_id HAVING n > 1
        )
        SELECT
            (SELECT coalesce(sum(n - 1), 0) FROM counts),
            (SELECT string_agg(pitch_id, ', ')
             FROM (SELECT pitch_id FROM counts ORDER BY pitch_id LIMIT 5))
        """
    ).fetchall()[0]
    detail = (
        "every pitch_id is unique"
        if dupes == 0
        else f"{dupes} rows share a pitch_id with different values, e.g. {examples}"
    )
    return _result(ctx, "pitch_id_unique", Severity.HARD, dupes, detail)


def _game_date_matches_partition(ctx: AuditContext) -> CheckResult:
    p = ctx.profile
    detail = (
        "every row's game_date matches its bronze partition"
        if p.date_mismatches == 0
        else f"{p.date_mismatches:,} rows dated differently from their partition, "
        f"e.g. {p.date_mismatch_examples}"
    )
    return _result(ctx, "game_date_matches_partition", Severity.HARD, p.date_mismatches, detail)


def _columns_populated(ctx: AuditContext) -> CheckResult:
    """Columns entirely empty in a season where Savant should populate them."""
    expected = [c.name for c in SOURCE_COLUMNS if c.first_season <= ctx.season]
    counts = ctx.con.execute(
        "SELECT " + ", ".join(f'count("{c}")' for c in expected) + " FROM audited"
    ).fetchall()[0]
    empty = [c for c, n in zip(expected, counts, strict=True) if n == 0]
    detail = (
        f"every column expected by {ctx.season} has values"
        if not empty
        else f"empty although expected by {ctx.season}: {', '.join(empty)}"
    )
    return _result(ctx, "columns_populated", Severity.WARN, len(empty), detail)


def _description_type_consistent(ctx: AuditContext) -> CheckResult:
    expected_type = (
        "CASE description "
        + " ".join(f"WHEN '{d}' THEN '{t}'" for d, t in DESCRIPTION_TYPES.items())
        + " END"
    )
    known = f"events IN {_sql_list(KNOWN_TYPE_EXCEPTIONS)}"
    # Unknown descriptions are description_known's failure, not this check's.
    mismatch = f"description IN {_sql_list(DESCRIPTION_TYPES)} AND type <> {expected_type}"
    result = _row_check(
        ctx,
        "description_type_consistent",
        Severity.WARN,
        where=f"{mismatch} AND {known} IS NOT TRUE",
        example="description || '/' || type || ' ' || coalesce(events, '') || ' ' || pitch_id",
        ok="every type matches its description",
        bad="rows whose type disagrees with their description",
    )
    (excused,) = ctx.con.execute(
        f"SELECT count(*) FROM audited WHERE {mismatch} AND {known}"
    ).fetchall()[0]
    names = ", ".join(KNOWN_TYPE_EXCEPTIONS.values())
    return replace(result, detail=f"{result.detail}; {excused:,} {names} excused")


def _sql_list(values: Iterable[str]) -> str:
    """A SQL list literal: ('a', 'b'). Values are contract constants, not input."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"
