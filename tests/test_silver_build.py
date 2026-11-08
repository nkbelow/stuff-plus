import datetime as dt

import duckdb
import pytest
from conftest import GAME_DATE, silver_pitch, write_bronze

from stuffplus.config import DataConfig, SeasonWindow
from stuffplus.ingest.bronze import META_FILE, partition_dir
from stuffplus.silver.build import (
    SilverAuditError,
    SilverBuildError,
    build_season,
    build_seasons,
    dq_results_path,
    pitches_path,
)

NEXT_DAY = GAME_DATE + dt.timedelta(days=1)
EARLY = dt.datetime(2026, 11, 7, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def cfg(tmp_path):
    return DataConfig(
        data_root=tmp_path,
        seasons={2024: SeasonWindow(start=dt.date(2024, 3, 15), end=dt.date(2024, 10, 6))},
    )


def published_rows(cfg, query="SELECT * FROM t"):
    path = pitches_path(cfg.silver_dir, 2024)
    return duckdb.sql(query.replace("t", f"read_parquet('{path}')", 1)).fetchall()


def test_build_publishes_typed_sorted_season_with_lineage(cfg):
    # Written out of order across two days, to prove the build sorts.
    write_bronze(cfg.bronze_dir, NEXT_DAY, [silver_pitch(1, game_date=NEXT_DAY, game_pk="745002")])
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(2), silver_pitch(1)], fetched_at=EARLY)

    result = build_season(cfg, 2024)

    assert result.rows == 3
    assert [r.passed for r in result.results] == [True]
    path = pitches_path(cfg.silver_dir, 2024)
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{path}')")
    types = dict(zip(rel.columns, map(str, rel.types), strict=True))
    assert types["release_speed"] == "DOUBLE"
    assert types["spin_axis"] == "DOUBLE"  # integral-looking measurements stay DOUBLE
    assert types["game_date"] == "DATE"
    assert types["pitcher"] == "BIGINT"
    assert "des" not in types  # only schema contract columns reach silver
    ids = rel.query("r", "SELECT pitch_id, bronze_fetched_at FROM r").fetchall()
    assert [i for i, _ in ids] == ["745000-1-1", "745000-1-2", "745002-1-1"]
    assert ids[0][1] == EARLY


def test_rebuild_produces_identical_content(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(i) for i in range(1, 6)])
    build_season(cfg, 2024)
    first = published_rows(cfg)
    build_season(cfg, 2024)
    assert published_rows(cfg) == first


def test_exact_duplicate_rows_collapse(cfg):
    row = silver_pitch(1)
    write_bronze(cfg.bronze_dir, GAME_DATE, [row, row, silver_pitch(2)])
    assert build_season(cfg, 2024).rows == 2


def test_conflicting_duplicate_blocks_publish_and_keeps_previous_season(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1), silver_pitch(2)])
    build_season(cfg, 2024)
    before = published_rows(cfg)

    write_bronze(
        cfg.bronze_dir,
        GAME_DATE,
        [silver_pitch(1), silver_pitch(2), silver_pitch(2, release_speed="88.0")],
    )
    with pytest.raises(SilverAuditError, match="745000-1-2"):
        build_season(cfg, 2024)

    assert published_rows(cfg) == before
    results = duckdb.sql(
        f"SELECT check_name, passed FROM read_parquet('{dq_results_path(cfg.silver_dir, 2024)}')"
    ).fetchall()
    assert results == [("pitch_id_unique", False)]


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


def test_build_seasons_continues_past_a_failing_season(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    summary = build_seasons(cfg, [2023, 2024])
    assert [r.season for r in summary.built] == [2024]
    assert list(summary.failed) == [2023]
