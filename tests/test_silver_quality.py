import contextlib
import datetime as dt
import json
from typing import Any

import duckdb
import pytest

from stuffplus.config import Bounds, DataConfig
from stuffplus.ingest.bronze import META_FILE, partition_dir, write_partition
from stuffplus.ingest.savant import FetchResult
from stuffplus.silver.build import SilverAuditError, SilverBuildError, audit_season, build_season
from stuffplus.silver.layout import SilverLayout
from tests.conftest import (
    GAME_DATE,
    SILVER_FIXTURE_HEADER,
    savant_csv,
    silver_pitch,
    write_bronze,
)

SEASON = 2024


@pytest.fixture
def clean_cfg(cfg: DataConfig) -> DataConfig:
    """Fixture games are tiny; real ones have 150+ pitches."""
    cfg.quality.pitches_per_game = Bounds(min=1, max=450)
    return cfg


def results(cfg: DataConfig, season: int = SEASON) -> dict[str, tuple[bool, str]]:
    """The last audit's results as written to disk: check name -> (passed, detail)."""
    path = SilverLayout(cfg.silver_dir).dq_results(season)
    rows = duckdb.execute(f"SELECT check_name, passed, detail FROM read_parquet('{path}')")
    return {name: (passed, detail) for name, passed, detail in rows.fetchall()}


def write_unvalidated(cfg: DataConfig, rows: list[dict[str, str]]) -> None:
    """Write a bronze day the fetcher's validator would reject (wrong date, game type)."""
    body = savant_csv(rows, header=SILVER_FIXTURE_HEADER)
    fetched_at = dt.datetime(2026, 11, 7, tzinfo=dt.UTC)
    write_partition(
        cfg.bronze_dir,
        FetchResult(GAME_DATE, "https://example.test", 200, fetched_at, body, len(rows), [], 0),
    )


def audit(
    cfg: DataConfig, *rows: dict[str, str], validated: bool = True, **bronze
) -> dict[str, tuple[bool, str]]:
    """Write one bronze day, build it, and return every check's result."""
    if validated:
        write_bronze(cfg.bronze_dir, GAME_DATE, list(rows), **bronze)
    else:
        write_unvalidated(cfg, list(rows))
    with contextlib.suppress(SilverAuditError):  # hard failures still write their results
        build_season(cfg, SEASON)
    return results(cfg)


def flagged(found: dict[str, tuple[bool, str]]) -> list[str]:
    return sorted(name for name, (passed, _) in found.items() if not passed)


def test_clean_data_passes_every_check(clean_cfg):
    found = audit(clean_cfg, silver_pitch(1), silver_pitch(2))
    assert flagged(found) == []
    assert found["row_reconciliation"][1].startswith("silver 2 = bronze 2 − 0 exact duplicates")


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"game_type": "S"}, "game_type_regular"),
        ({"batter": ""}, "identifiers_not_null"),
        ({"description": "new_outcome"}, "description_known"),
        ({"spin_dir": "12"}, "deprecated_columns_empty"),
    ],
)
def test_hard_check_blocks_publish(clean_cfg, overrides, check):
    # game_type is also rejected at fetch time; silver asserts it again.
    rows = (silver_pitch(1), silver_pitch(2, **overrides))
    found = audit(clean_cfg, *rows, validated="game_type" not in overrides)
    assert flagged(found) == [check]
    assert not SilverLayout(clean_cfg.silver_dir).pitches(SEASON).exists()


def test_missing_contract_column_fails_and_names_it(clean_cfg):
    header = [c for c in SILVER_FIXTURE_HEADER if c != "zone"]
    found = audit(clean_cfg, silver_pitch(1), header=header)
    assert "schema_contract" in flagged(found)
    assert "zone (1 files from 2024-04-12)" in found["schema_contract"][1]


def test_new_bronze_column_warns_and_is_listed(clean_cfg):
    header = [*SILVER_FIXTURE_HEADER, "pitch_speed_v2"]
    found = audit(clean_cfg, silver_pitch(1), header=header)
    assert flagged(found) == ["schema_unknown_columns"]
    assert "pitch_speed_v2" in found["schema_unknown_columns"][1]


def test_game_date_must_match_partition(clean_cfg):
    next_day = GAME_DATE + dt.timedelta(days=1)
    found = audit(clean_cfg, silver_pitch(1), silver_pitch(2, game_date=next_day), validated=False)
    assert flagged(found) == ["game_date_matches_partition"]
    assert "2024-04-13 in partition 2024-04-12" in found["game_date_matches_partition"][1]


def test_row_reconciliation_catches_rows_missing_from_silver(clean_cfg):
    write_bronze(clean_cfg.bronze_dir, GAME_DATE, [silver_pitch(1), silver_pitch(2)])
    # The fetcher recorded three rows, but only two reach silver.
    meta_path = partition_dir(clean_cfg.bronze_dir, GAME_DATE) / META_FILE
    meta_path.write_text(json.dumps(json.loads(meta_path.read_text()) | {"rows": 3}))
    with pytest.raises(SilverAuditError, match="row_reconciliation"):
        build_season(clean_cfg, SEASON)


def test_blocked_publish_names_every_failing_check_and_keeps_previous_season(clean_cfg):
    write_bronze(clean_cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    build_season(clean_cfg, SEASON)
    published = SilverLayout(clean_cfg.silver_dir).pitches(SEASON)
    before = published.read_bytes()

    write_unvalidated(clean_cfg, [silver_pitch(1, game_type="S", description="new_outcome")])
    with pytest.raises(SilverAuditError) as exc:
        build_season(clean_cfg, SEASON)
    assert {r.check_name for r in exc.value.failed} == {"game_type_regular", "description_known"}
    assert "game_type_regular" in str(exc.value) and "description_known" in str(exc.value)
    assert published.read_bytes() == before


BAT_TRACKING = {
    c: ""
    for c in (
        "bat_speed",
        "swing_length",
        "miss_distance",
        "attack_angle",
        "attack_direction",
        "swing_path_tilt",
        "intercept_ball_minus_batter_pos_x_inches",
        "intercept_ball_minus_batter_pos_y_inches",
    )
}


def test_column_empty_before_its_first_season_is_expected(clean_cfg):
    day = dt.date(2022, 4, 12)
    write_bronze(clean_cfg.bronze_dir, day, [silver_pitch(1, game_date=day) | BAT_TRACKING])
    build_season(clean_cfg, 2022)
    assert results(clean_cfg, 2022)["columns_populated"][0]


def test_column_empty_after_its_first_season_warns(clean_cfg):
    found = audit(clean_cfg, silver_pitch(1) | BAT_TRACKING)
    assert flagged(found) == ["columns_populated"]
    assert "bat_speed" in found["columns_populated"][1]


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"release_speed": "120"}, "range_release_speed"),
        ({"pfx_x": "-3.5"}, "range_pfx_x"),
        ({"type": "B"}, "description_type_consistent"),
        (
            {"description": "hit_into_play", "type": "X", "launch_speed": ""},
            "bip_launch_speed",
        ),
        ({"pitch_type": ""}, "pitch_type_missing"),
    ],
)
def test_warn_check_flags_but_publishes(clean_cfg, overrides, check):
    found = audit(clean_cfg, silver_pitch(1), silver_pitch(2, **overrides))
    assert flagged(found) == [check]
    assert SilverLayout(clean_cfg.silver_dir).pitches(SEASON).exists()


def test_known_exceptions_are_excused(clean_cfg):
    catcher_interference = silver_pitch(
        2, description="hit_into_play", type="S", events="catcher_interf", launch_speed=""
    )
    pitch_clock = silver_pitch(3, description="automatic_ball", type="B", pitch_type="")
    found = audit(clean_cfg, silver_pitch(1), catcher_interference, pitch_clock)
    assert flagged(found) == []
    assert found["description_type_consistent"][1].endswith("1 catcher interference excused")


def games(cfg: DataConfig) -> list[tuple[Any, ...]]:
    path = SilverLayout(cfg.silver_dir).dq("dq_games", SEASON)
    query = f"SELECT game_pk, pitches, spin_coverage, flags FROM read_parquet('{path}')"
    return duckdb.execute(query).fetchall()


def test_game_without_spin_is_a_tracking_outage(clean_cfg):
    no_spin = {"release_spin_rate": ""}
    found = audit(
        clean_cfg,
        silver_pitch(1) | no_spin,
        silver_pitch(2) | no_spin,
        silver_pitch(1, game_pk="745001"),
        # Automatic calls aren't thrown, so their missing spin doesn't count.
        silver_pitch(2, game_pk="745001", description="automatic_ball", type="B") | no_spin,
    )
    assert flagged(found) == ["spin_outage_games"]
    assert games(clean_cfg) == [(745000, 2, 0.0, ["spin_outage"]), (745001, 2, 1.0, [])]


def test_short_game_is_listed(cfg):
    found = audit(cfg, silver_pitch(1), silver_pitch(2))  # default range: 150–450
    assert flagged(found) == ["pitches_per_game"]
    assert games(cfg) == [(745000, 2, 1.0, ["pitch_count"])]


def test_null_rates_report_core_physics_by_month(clean_cfg):
    audit(clean_cfg, silver_pitch(1), silver_pitch(2, release_spin_rate=""))
    path = SilverLayout(clean_cfg.silver_dir).dq("dq_null_rates", SEASON)
    rows = duckdb.execute(
        f"SELECT season, month, pitches_thrown, null_pct FROM read_parquet('{path}') "
        "WHERE column_name = 'release_spin_rate'"
    ).fetchall()
    assert rows == [(SEASON, 4, 2, 50.0)]


def test_reaudit_rewrites_results_without_touching_published_data(clean_cfg):
    write_bronze(clean_cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    build_season(clean_cfg, SEASON)
    layout = SilverLayout(clean_cfg.silver_dir)
    before = layout.pitches(SEASON).read_bytes()
    layout.dq_results(SEASON).unlink()

    found = audit_season(clean_cfg, SEASON)

    assert all(r.passed for r in found)
    assert layout.pitches(SEASON).read_bytes() == before
    assert flagged(results(clean_cfg)) == []


def test_reaudit_of_unpublished_season_raises(clean_cfg):
    write_bronze(clean_cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    with pytest.raises(SilverBuildError, match="not published"):
        audit_season(clean_cfg, SEASON)
