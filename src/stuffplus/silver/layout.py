"""Where silver files live. The one definition of the layout, shared by build and catalog.

  pitches/season=YYYY/part.parquet              published seasons
  _staging/pitches/season=YYYY/part.parquet     write–audit–publish scratch area: the last
                                                build before publishing (not a data layer)
  dq/dq_results/season=YYYY/part.parquet        the last audit's results

Pass `season="*"` to get a glob over every season.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Season = int | Literal["*"]


@dataclass(frozen=True)
class SilverLayout:
    root: Path

    def pitches(self, season: Season) -> Path:
        return self.root / "pitches" / f"season={season}" / "part.parquet"

    def staging(self, season: Season) -> Path:
        return self.root / "_staging" / "pitches" / f"season={season}" / "part.parquet"

    def dq_results(self, season: Season) -> Path:
        return self.root / "dq" / "dq_results" / f"season={season}" / "part.parquet"
