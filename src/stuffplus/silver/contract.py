"""The silver schema contract: one entry per column in a silver season file.

Names and units are Savant's, unchanged (ADR 0006); units are recorded here, not converted.
Groups say when a value is known relative to the pitch, so later layers can classify
columns: gold's leakage test forbids `OUTCOME`, `BAT_TRACKING` and `FUTURE` columns as
features. The build generates its casts and its output column list from this module, and a
test asserts every published file matches it exactly.

`SOURCE_COLUMNS` are cast from bronze, in Savant's header order; `DERIVED_COLUMNS` are
computed by the build. The catalog adds one more column, `season`, read from the
partition path. Types and first populated seasons were confirmed against 2022–2026 bronze
(#11): every season sends all 119 columns, with columns that didn't exist yet left empty.
"""

from dataclasses import dataclass
from enum import StrEnum


class Group(StrEnum):
    ID = "id"  # identifies the pitch or the pitcher/batter
    CONTEXT = "context"  # the situation before the pitch: count, bases, score, fielders
    SAVANT_MODEL = "savant_model"  # Savant's classification of the pitch (pitch type)
    PHYSICS = "physics"  # how the ball left the hand and moved
    LOCATION = "location"  # where it crossed the plate (command, not stuff)
    OUTCOME = "outcome"  # what happened on or after the pitch
    BAT_TRACKING = "bat_tracking"  # the hitter's swing (from mid-2023)
    FUTURE = "future"  # known only after the game: a leakage trap, not an outcome
    DERIVED = "derived"  # computed by the silver build, not sent by Savant


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # DuckDB type
    group: Group
    unit: str | None = None  # None: a code, count, ID, probability or other unitless value
    # First season Savant populates it; None means every season this project ingests.
    first_season: int | None = None


# Savant's `pitch_number` is the pitch's position within the plate appearance, so the
# three together identify a pitch within a game; `pitch_id` joins them with '-'.
KEY_COLUMNS = ("game_pk", "at_bat_number", "pitch_number")
SORT_COLUMNS = ("game_date", *KEY_COLUMNS)
IDENTIFIER_COLUMNS = (*KEY_COLUMNS, "pitcher", "batter", "game_date")

# Types: measurements are DOUBLE even where every value seen is whole (spin, axis, launch
# angle, distance), so a decimal on some later date can't change the type. IDs are BIGINT,
# counts and codes INTEGER, labels VARCHAR. Positions and movement are in the catcher's
# view: x is positive toward first base.
BAT_TRACKING_FROM = 2023  # Savant's bat tracking starts 2023-07-14

SOURCE_COLUMNS: tuple[Column, ...] = (
    Column("pitch_type", "VARCHAR", Group.SAVANT_MODEL),
    Column("game_date", "DATE", Group.CONTEXT),
    Column("release_speed", "DOUBLE", Group.PHYSICS, "mph"),
    Column("release_pos_x", "DOUBLE", Group.PHYSICS, "ft"),
    Column("release_pos_z", "DOUBLE", Group.PHYSICS, "ft"),
    Column("player_name", "VARCHAR", Group.ID),  # the pitcher, "Last, First"
    Column("batter", "BIGINT", Group.ID),
    Column("pitcher", "BIGINT", Group.ID),
    Column("events", "VARCHAR", Group.OUTCOME),  # plate appearance result, on its last pitch
    Column("description", "VARCHAR", Group.OUTCOME),  # pitch result; vocabulary below
    Column("zone", "INTEGER", Group.LOCATION),  # 1–9 in the zone, 11–14 outside
    Column("des", "VARCHAR", Group.OUTCOME),  # play-by-play text
    Column("game_type", "VARCHAR", Group.CONTEXT),
    Column("stand", "VARCHAR", Group.CONTEXT),
    Column("p_throws", "VARCHAR", Group.CONTEXT),
    Column("home_team", "VARCHAR", Group.CONTEXT),
    Column("away_team", "VARCHAR", Group.CONTEXT),
    Column("type", "VARCHAR", Group.OUTCOME),  # B ball, S strike, X in play
    Column("hit_location", "INTEGER", Group.OUTCOME),  # fielder position 1–9
    Column("bb_type", "VARCHAR", Group.OUTCOME),
    Column("balls", "INTEGER", Group.CONTEXT),
    Column("strikes", "INTEGER", Group.CONTEXT),
    Column("game_year", "INTEGER", Group.CONTEXT),
    Column("pfx_x", "DOUBLE", Group.PHYSICS, "ft"),  # movement vs. a spinless ball
    Column("pfx_z", "DOUBLE", Group.PHYSICS, "ft"),
    Column("plate_x", "DOUBLE", Group.LOCATION, "ft"),  # mid-plate from 2026, front before
    Column("plate_z", "DOUBLE", Group.LOCATION, "ft"),
    Column("on_3b", "BIGINT", Group.CONTEXT),  # runner's player ID, empty if base open
    Column("on_2b", "BIGINT", Group.CONTEXT),
    Column("on_1b", "BIGINT", Group.CONTEXT),
    Column("outs_when_up", "INTEGER", Group.CONTEXT),
    Column("inning", "INTEGER", Group.CONTEXT),
    Column("inning_topbot", "VARCHAR", Group.CONTEXT),
    Column("hc_x", "DOUBLE", Group.OUTCOME, "px"),  # spray chart image coordinates
    Column("hc_y", "DOUBLE", Group.OUTCOME, "px"),
    # Trajectory fit at y = 50 ft from home plate.
    Column("vx0", "DOUBLE", Group.PHYSICS, "ft/s"),
    Column("vy0", "DOUBLE", Group.PHYSICS, "ft/s"),
    Column("vz0", "DOUBLE", Group.PHYSICS, "ft/s"),
    Column("ax", "DOUBLE", Group.PHYSICS, "ft/s^2"),
    Column("ay", "DOUBLE", Group.PHYSICS, "ft/s^2"),
    Column("az", "DOUBLE", Group.PHYSICS, "ft/s^2"),
    Column("sz_top", "DOUBLE", Group.LOCATION, "ft"),  # ABS-defined from 2026
    Column("sz_bot", "DOUBLE", Group.LOCATION, "ft"),
    Column("hit_distance_sc", "DOUBLE", Group.OUTCOME, "ft"),
    Column("launch_speed", "DOUBLE", Group.OUTCOME, "mph"),
    Column("launch_angle", "DOUBLE", Group.OUTCOME, "deg"),
    Column("effective_speed", "DOUBLE", Group.PHYSICS, "mph"),  # velocity adjusted for extension
    Column("release_spin_rate", "DOUBLE", Group.PHYSICS, "rpm"),
    Column("release_extension", "DOUBLE", Group.PHYSICS, "ft"),
    Column("game_pk", "BIGINT", Group.ID),
    *(Column(f"fielder_{n}", "BIGINT", Group.CONTEXT) for n in range(2, 10)),  # 2 is the catcher
    Column("release_pos_y", "DOUBLE", Group.PHYSICS, "ft"),
    Column("estimated_ba_using_speedangle", "DOUBLE", Group.OUTCOME),
    Column("estimated_woba_using_speedangle", "DOUBLE", Group.OUTCOME),
    Column("woba_value", "DOUBLE", Group.OUTCOME),
    Column("woba_denom", "INTEGER", Group.OUTCOME),
    Column("babip_value", "INTEGER", Group.OUTCOME),
    Column("iso_value", "INTEGER", Group.OUTCOME),
    Column("launch_speed_angle", "INTEGER", Group.OUTCOME),  # contact quality code 1–6
    Column("at_bat_number", "INTEGER", Group.ID),
    Column("pitch_number", "INTEGER", Group.ID),
    Column("pitch_name", "VARCHAR", Group.SAVANT_MODEL),
    Column("home_score", "INTEGER", Group.CONTEXT),
    Column("away_score", "INTEGER", Group.CONTEXT),
    Column("bat_score", "INTEGER", Group.CONTEXT),
    Column("fld_score", "INTEGER", Group.CONTEXT),
    Column("post_away_score", "INTEGER", Group.OUTCOME),
    Column("post_home_score", "INTEGER", Group.OUTCOME),
    Column("post_bat_score", "INTEGER", Group.OUTCOME),
    Column("post_fld_score", "INTEGER", Group.OUTCOME),
    Column("if_fielding_alignment", "VARCHAR", Group.CONTEXT),
    Column("of_fielding_alignment", "VARCHAR", Group.CONTEXT),
    Column("spin_axis", "DOUBLE", Group.PHYSICS, "deg"),
    Column("delta_home_win_exp", "DOUBLE", Group.OUTCOME),
    Column("delta_run_exp", "DOUBLE", Group.OUTCOME, "runs"),
    Column("bat_speed", "DOUBLE", Group.BAT_TRACKING, "mph", BAT_TRACKING_FROM),
    Column("swing_length", "DOUBLE", Group.BAT_TRACKING, "ft", BAT_TRACKING_FROM),
    # Inches, inferred from the distribution (2024 median 1.5, 99th percentile 18).
    Column("miss_distance", "DOUBLE", Group.BAT_TRACKING, "in", BAT_TRACKING_FROM),
    Column("estimated_slg_using_speedangle", "DOUBLE", Group.OUTCOME),
    Column("delta_pitcher_run_exp", "DOUBLE", Group.OUTCOME, "runs"),
    Column("hyper_speed", "DOUBLE", Group.OUTCOME, "mph"),  # adjusted exit velocity
    Column("home_score_diff", "INTEGER", Group.CONTEXT),
    Column("bat_score_diff", "INTEGER", Group.CONTEXT),
    # Before the pitch: this pitch's value plus its delta is the next pitch's value
    # (99.9% of 2024 pitches), so these are situation, not outcome.
    Column("home_win_exp", "DOUBLE", Group.CONTEXT),
    Column("bat_win_exp", "DOUBLE", Group.CONTEXT),
    Column("age_pit_legacy", "INTEGER", Group.CONTEXT, "years"),
    Column("age_bat_legacy", "INTEGER", Group.CONTEXT, "years"),
    Column("age_pit", "INTEGER", Group.CONTEXT, "years"),
    Column("age_bat", "INTEGER", Group.CONTEXT, "years"),
    Column("n_thruorder_pitcher", "INTEGER", Group.CONTEXT),  # times through the order
    Column("n_priorpa_thisgame_player_at_bat", "INTEGER", Group.CONTEXT),
    Column("pitcher_days_since_prev_game", "INTEGER", Group.CONTEXT, "days"),
    Column("batter_days_since_prev_game", "INTEGER", Group.CONTEXT, "days"),
    Column("pitcher_days_until_next_game", "INTEGER", Group.FUTURE, "days"),
    Column("batter_days_until_next_game", "INTEGER", Group.FUTURE, "days"),
    # Feet despite the name "break": api_break_x_arm averages 0.64 on 2024 four-seamers,
    # |pfx_x| 0.65. _arm is arm side positive; _batter_in is toward the batter positive.
    Column("api_break_z_with_gravity", "DOUBLE", Group.PHYSICS, "ft"),
    Column("api_break_x_arm", "DOUBLE", Group.PHYSICS, "ft"),
    Column("api_break_x_batter_in", "DOUBLE", Group.PHYSICS, "ft"),
    Column("arm_angle", "DOUBLE", Group.PHYSICS, "deg"),
    Column("attack_angle", "DOUBLE", Group.BAT_TRACKING, "deg", BAT_TRACKING_FROM),
    Column("attack_direction", "DOUBLE", Group.BAT_TRACKING, "deg", BAT_TRACKING_FROM),
    Column("swing_path_tilt", "DOUBLE", Group.BAT_TRACKING, "deg", BAT_TRACKING_FROM),
    Column(
        "intercept_ball_minus_batter_pos_x_inches",
        "DOUBLE",
        Group.BAT_TRACKING,
        "in",
        BAT_TRACKING_FROM,
    ),
    Column(
        "intercept_ball_minus_batter_pos_y_inches",
        "DOUBLE",
        Group.BAT_TRACKING,
        "in",
        BAT_TRACKING_FROM,
    ),
)

# Sent by Savant but empty in every season 2022–2026 (the pre-Statcast tracking system).
# The audit confirms they are still empty, then the build drops them. A file without them
# is fine: they aren't expected.
DEPRECATED_COLUMNS = (
    "spin_dir",
    "spin_rate_deprecated",
    "break_angle_deprecated",
    "break_length_deprecated",
    "tfs_deprecated",
    "tfs_zulu_deprecated",
    "umpire",
    "sv_id",
)

DERIVED_COLUMNS: tuple[Column, ...] = (
    Column("pitch_id", "VARCHAR", Group.DERIVED),
    Column("bronze_fetched_at", "TIMESTAMP WITH TIME ZONE", Group.DERIVED),
)

# Column order in a published silver file.
SILVER_COLUMNS: tuple[Column, ...] = (DERIVED_COLUMNS[0], *SOURCE_COLUMNS, DERIVED_COLUMNS[1])

# Every `description` Savant sends, with the `type` (B/S/X) it implies. A value outside this
# vocabulary blocks publishing: gold's outcome classes must be extended deliberately.
# swinging_pitchout first appeared in 2026 (one pitch, 2026-05-09).
DESCRIPTION_TYPES: dict[str, str] = {
    "ball": "B",
    "blocked_ball": "B",
    "hit_by_pitch": "B",
    "automatic_ball": "B",
    "pitchout": "B",
    "called_strike": "S",
    "swinging_strike": "S",
    "swinging_strike_blocked": "S",
    "foul": "S",
    "foul_tip": "S",
    "foul_bunt": "S",
    "bunt_foul_tip": "S",
    "missed_bunt": "S",
    "automatic_strike": "S",
    "swinging_pitchout": "S",
    "hit_into_play": "X",
}

# Pitch-clock violations: a ball or strike is added to the count and no pitch is thrown, so
# these rows mostly have no physics or pitch type.
AUTOMATIC_CALLS = ("automatic_ball", "automatic_strike")
