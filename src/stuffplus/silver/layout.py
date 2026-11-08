"""Where silver files live. The one definition of the layout, shared by build and catalog.

  pitches/season=YYYY/part.parquet              published seasons
  _staging/pitches/season=YYYY/part.parquet     write–audit–publish scratch area: the last
                                                build before publishing (not a data layer)
  dq/<report>/season=YYYY/part.parquet          the last audit's outputs, one per report:
                                                dq_results, dq_null_rates, dq_games

Pass `season="*"` to get a glob over every season.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from stuffplus.fs import hive_seasons

Season = int | Literal["*"]


class Report(StrEnum):
    """The data quality outputs each audit writes, one file per season."""

    RESULTS = "dq_results"
    NULL_RATES = "dq_null_rates"
    GAMES = "dq_games"


@dataclass(frozen=True)
class SilverLayout:
    root: Path

    def pitches(self, season: Season) -> Path:
        return self.root / "pitches" / f"season={season}" / "part.parquet"

    def staging(self, season: Season) -> Path:
        return self.root / "_staging" / "pitches" / f"season={season}" / "part.parquet"

    def dq(self, report: Report, season: Season) -> Path:
        return self.root / "dq" / report / f"season={season}" / "part.parquet"

    def published_seasons(self) -> list[int]:
        return [s for s in hive_seasons(self.root / "pitches") if self.pitches(s).exists()]
