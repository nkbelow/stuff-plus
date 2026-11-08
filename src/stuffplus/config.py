"""Typed configuration loaded from YAML files under configs/."""

import datetime as dt
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_CONFIG = REPO_ROOT / "configs" / "data.yaml"
DATA_ROOT_ENV = "STUFFPLUS_DATA_ROOT"


class SeasonWindow(BaseModel):
    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _ordered(self) -> SeasonWindow:
        if self.end < self.start:
            raise ValueError(f"season window ends before it starts: {self.start} > {self.end}")
        return self


class SavantConfig(BaseModel):
    base_url: str = "https://baseballsavant.mlb.com/statcast_search/csv"
    timeout_s: float = 120.0
    max_retries: int = 5
    backoff_s: float = 2.0
    min_interval_s: float = 1.0
    user_agent: str = "stuff-plus/0.1 (personal research project)"


class DataConfig(BaseModel):
    data_root: Path
    seasons: dict[int, SeasonWindow]
    savant: SavantConfig = SavantConfig()

    @property
    def bronze_dir(self) -> Path:
        return self.data_root / "bronze" / "statcast"

    @property
    def silver_dir(self) -> Path:
        return self.data_root / "silver"

    @property
    def catalog_path(self) -> Path:
        return self.data_root / "stuff.duckdb"

    def season_dates(self, season: int) -> list[dt.date]:
        if season not in self.seasons:
            raise KeyError(f"season {season} not configured; known: {sorted(self.seasons)}")
        window = self.seasons[season]
        return date_range(window.start, window.end)


def date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of dates from start to end."""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def load_data_config(path: Path = DEFAULT_DATA_CONFIG) -> DataConfig:
    """Load the data config. STUFFPLUS_DATA_ROOT, if set, overrides `data_root`."""
    raw = yaml.safe_load(path.read_text())
    if override := os.environ.get(DATA_ROOT_ENV, "").strip():
        raw["data_root"] = override
    cfg = DataConfig.model_validate(raw)
    if not cfg.data_root.is_absolute():
        cfg.data_root = (path.resolve().parent.parent / cfg.data_root).resolve()
    return cfg
