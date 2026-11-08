import datetime as dt
from pathlib import Path

import duckdb
import pytest

from stuffplus.config import DataConfig
from stuffplus.ingest.bronze import META_FILE, partition_dir
from stuffplus.silver.build import (
    SilverAuditError,
    SilverBuildError,
    build_season,
    build_seasons,
)
from stuffplus.silver.contract import SILVER_COLUMNS
from stuffplus.silver.layout import SilverLayout
from stuffplus.silver.quality import has_hard_failure
from tests.conftest import GAME_DATE, SILVER_FIXTURE_HEADER, silver_pitch, write_bronze

NEXT_DAY = GAME_DATE + dt.timedelta(days=1)
EARLY = dt.datetime(2026, 11, 7, 9, 0, tzinfo=dt.UTC)


def published(cfg: DataConfig) -> Path:
    return SilverLayout(cfg.silver_dir).pitches(2024)


def content_hash(path: Path) -> tuple[int, str]:
    """Row count and a hash of every row's values, in pitch_id order."""
    (row,) = duckdb.execute(
        f"SELECT count(*), md5(string_agg(md5(t::VARCHAR), '' ORDER BY pitch_id)) "
        f"FROM read_parquet('{path}') t"
    ).fetchall()
    return row


def test_published_file_matches_the_schema_contract_exactly(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    build_season(cfg, 2024)
    # hive_partitioning off: inspect the file itself, not the `season` DuckDB infers from the path.
    columns = duckdb.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{published(cfg)}', hive_partitioning = false)"
    ).fetchall()
    assert [(name, kind) for name, kind, *_ in columns] == [
        (c.name, c.type) for c in SILVER_COLUMNS
    ]


def test_build_sorts_and_attaches_lineage(cfg):
    # Written out of order across two days, to prove the build sorts.
    write_bronze(cfg.bronze_dir, NEXT_DAY, [silver_pitch(1, game_date=NEXT_DAY, game_pk="745002")])
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(2), silver_pitch(1)], fetched_at=EARLY)

    result = build_season(cfg, 2024)

    assert result.rows == 3
    assert not has_hard_failure(result.results)
    rows = duckdb.execute(
        f"SELECT pitch_id, bronze_fetched_at FROM read_parquet('{published(cfg)}')"
    ).fetchall()
    assert [pitch_id for pitch_id, _ in rows] == ["745000-1-1", "745000-1-2", "745002-1-1"]
    assert rows[0][1] == EARLY


def test_rebuild_produces_identical_content(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(i) for i in range(1, 6)])
    build_season(cfg, 2024)
    first = content_hash(published(cfg))
    build_season(cfg, 2024)
    assert content_hash(published(cfg)) == first


def test_exact_duplicate_rows_collapse(cfg):
    row = silver_pitch(1)
    write_bronze(cfg.bronze_dir, GAME_DATE, [row, row, silver_pitch(2)])
    assert build_season(cfg, 2024).rows == 2


def test_rows_differing_only_outside_the_contract_are_not_duplicates(cfg):
    # `new_col` isn't a silver column, but a difference there still means the rows conflict.
    write_bronze(
        cfg.bronze_dir,
        GAME_DATE,
        [silver_pitch(1, new_col="one"), silver_pitch(1, new_col="two")],
        header=[*SILVER_FIXTURE_HEADER, "new_col"],
    )
    with pytest.raises(SilverAuditError, match="745000-1-1"):
        build_season(cfg, 2024)


def test_conflicting_duplicate_blocks_publish_and_keeps_previous_season(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1), silver_pitch(2)])
    build_season(cfg, 2024)
    before = content_hash(published(cfg))

    write_bronze(
        cfg.bronze_dir,
        GAME_DATE,
        [silver_pitch(1), silver_pitch(2), silver_pitch(2, release_speed="88.0")],
    )
    with pytest.raises(SilverAuditError, match="745000-1-2"):
        build_season(cfg, 2024)

    assert content_hash(published(cfg)) == before
    results_file = SilverLayout(cfg.silver_dir).dq_results(2024)
    failed = duckdb.execute(
        f"SELECT check_name FROM read_parquet('{results_file}') "
        "WHERE NOT passed AND severity = 'hard'"
    )
    assert failed.fetchall() == [("pitch_id_unique",)]


def test_value_that_wont_cast_fails_naming_column_value_and_date(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1, release_speed="fast")])
    with pytest.raises(SilverBuildError, match="season 2024.*release_speed = 'fast' on 2024-04-12"):
        build_season(cfg, 2024)


def test_integer_columns_reject_decimals_instead_of_rounding(cfg):
    # DuckDB's own CAST('1.5' AS INTEGER) returns 2.
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1, balls="1.5")])
    with pytest.raises(SilverBuildError, match="balls = '1.5' on 2024-04-12: not a INTEGER"):
        build_season(cfg, 2024)


def test_every_column_keeps_its_value_and_type(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1, des="")])
    build_season(cfg, 2024)
    file = f"read_parquet('{published(cfg)}', hive_partitioning = false)"
    row = duckdb.execute(f"SELECT * FROM {file}").fetchall()[0]
    by_name = dict(zip([c.name for c in SILVER_COLUMNS], row, strict=True))
    assert by_name["game_date"] == GAME_DATE
    assert by_name["balls"] == 0
    assert by_name["release_speed"] == 95.1
    assert by_name["fielder_2"] == 100
    assert by_name["des"] is None  # an empty bronze field is NULL


def test_incomplete_and_empty_partitions_are_skipped(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    write_bronze(cfg.bronze_dir, NEXT_DAY, [])  # off-day: header only
    later = NEXT_DAY + dt.timedelta(days=1)
    write_bronze(cfg.bronze_dir, later, [silver_pitch(1, game_date=later, game_pk="745009")])
    partition_dir(cfg.bronze_dir, later).joinpath(META_FILE).unlink()  # crashed mid-write

    assert build_season(cfg, 2024).rows == 1


def test_season_without_bronze_raises(cfg):
    with pytest.raises(SilverBuildError, match="2024"):
        build_season(cfg, 2024)


def test_build_seasons_continues_past_failing_seasons(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    write_bronze(
        cfg.bronze_dir,
        dt.date(2025, 4, 12),
        [silver_pitch(1, game_date=dt.date(2025, 4, 12), balls="x")],
    )
    summary = build_seasons(cfg, [2023, 2024, 2025])
    assert [r.season for r in summary.built] == [2024]
    assert sorted(summary.failed) == [2023, 2025]  # no bronze; a value that won't cast
