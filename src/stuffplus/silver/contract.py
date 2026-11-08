"""The silver schema contract: one entry per column in a silver season file.

Names and units are Savant's, unchanged (ADR 0006); units are recorded here, not converted.
Groups classify columns for later layers (gold's leakage test forbids `OUTCOME` columns as
features). The build generates its casts and its output column list from this module, and a
test asserts every published file matches it exactly.

`SOURCE_COLUMNS` are cast from bronze; `DERIVED_COLUMNS` are computed by the build. The
catalog adds one more column, `season`, read from the partition path.

This is the starter set for the tracer (#10). The full ~112-column contract arrives in #11.
"""

from dataclasses import dataclass
from enum import StrEnum


class Group(StrEnum):
    ID = "id"
    CONTEXT = "context"
    SAVANT_MODEL = "savant_model"  # Savant's own classifications (pitch type)
    OUTCOME = "outcome"
    PHYSICS = "physics"
    DERIVED = "derived"  # computed by the silver build, not sent by Savant


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # DuckDB type
    group: Group
    unit: str | None = None


# Savant's `pitch_number` is the pitch's position within the plate appearance, so the
# three together identify a pitch within a game; `pitch_id` joins them with '-'.
KEY_COLUMNS = ("game_pk", "at_bat_number", "pitch_number")
SORT_COLUMNS = ("game_date", *KEY_COLUMNS)

SOURCE_COLUMNS: tuple[Column, ...] = (
    Column("game_pk", "BIGINT", Group.ID),
    Column("at_bat_number", "INTEGER", Group.ID),
    Column("pitch_number", "INTEGER", Group.ID),
    Column("pitcher", "BIGINT", Group.ID),
    Column("batter", "BIGINT", Group.ID),
    Column("game_date", "DATE", Group.CONTEXT),
    Column("game_type", "VARCHAR", Group.CONTEXT),
    Column("home_team", "VARCHAR", Group.CONTEXT),
    Column("away_team", "VARCHAR", Group.CONTEXT),
    Column("p_throws", "VARCHAR", Group.CONTEXT),
    Column("stand", "VARCHAR", Group.CONTEXT),
    Column("balls", "INTEGER", Group.CONTEXT),
    Column("strikes", "INTEGER", Group.CONTEXT),
    Column("pitch_type", "VARCHAR", Group.SAVANT_MODEL),
    Column("description", "VARCHAR", Group.OUTCOME),
    Column("type", "VARCHAR", Group.OUTCOME),
    Column("events", "VARCHAR", Group.OUTCOME),
    # Measurements are DOUBLE even where today's values look integral (spin, axis):
    # a decimal on some later date must not change the type.
    Column("release_speed", "DOUBLE", Group.PHYSICS, unit="mph"),
    Column("release_spin_rate", "DOUBLE", Group.PHYSICS, unit="rpm"),
    Column("spin_axis", "DOUBLE", Group.PHYSICS, unit="deg"),
    Column("pfx_x", "DOUBLE", Group.PHYSICS, unit="ft"),  # catcher's perspective
    Column("pfx_z", "DOUBLE", Group.PHYSICS, unit="ft"),
    Column("release_pos_x", "DOUBLE", Group.PHYSICS, unit="ft"),  # catcher's perspective
    Column("release_pos_z", "DOUBLE", Group.PHYSICS, unit="ft"),
    Column("release_extension", "DOUBLE", Group.PHYSICS, unit="ft"),
    Column("arm_angle", "DOUBLE", Group.PHYSICS, unit="deg"),
)

DERIVED_COLUMNS: tuple[Column, ...] = (
    Column("pitch_id", "VARCHAR", Group.DERIVED),
    Column("bronze_fetched_at", "TIMESTAMP WITH TIME ZONE", Group.DERIVED),
)

# Column order in a published silver file.
SILVER_COLUMNS: tuple[Column, ...] = (DERIVED_COLUMNS[0], *SOURCE_COLUMNS, DERIVED_COLUMNS[1])
