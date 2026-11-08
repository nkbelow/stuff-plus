import datetime as dt

import pytest
from pydantic import ValidationError

from stuffplus.config import DATA_ROOT_ENV, DEFAULT_DATA_CONFIG, load_data_config

CONFIG = """
data_root: data
seasons:
  2024: {start: 2024-03-20, end: 2024-03-22}
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
    path = tmp_path / "configs" / "data.yaml"
    path.parent.mkdir()
    path.write_text(CONFIG)
    return path


def test_relative_data_root_resolves_against_repo_root(config_file, tmp_path):
    cfg = load_data_config(config_file)
    assert cfg.data_root == (tmp_path / "data").resolve()
    assert cfg.bronze_dir == cfg.data_root / "bronze" / "statcast"


def test_env_var_overrides_data_root(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "elsewhere"))
    assert load_data_config(config_file).data_root == tmp_path / "elsewhere"


def test_blank_env_var_is_ignored(config_file, tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ROOT_ENV, "  ")
    assert load_data_config(config_file).data_root == (tmp_path / "data").resolve()


def test_season_dates_are_inclusive(config_file):
    dates = load_data_config(config_file).season_dates(2024)
    assert dates == [dt.date(2024, 3, 20), dt.date(2024, 3, 21), dt.date(2024, 3, 22)]


def test_unknown_season_raises(config_file):
    with pytest.raises(KeyError, match="2019"):
        load_data_config(config_file).season_dates(2019)


def test_reversed_window_rejected(config_file):
    config_file.write_text(CONFIG.replace("end: 2024-03-22", "end: 2024-03-01"))
    with pytest.raises(ValidationError, match="ends before it starts"):
        load_data_config(config_file)


def test_repo_config_loads():
    cfg = load_data_config(DEFAULT_DATA_CONFIG)
    assert set(cfg.seasons) == {2022, 2023, 2024, 2025, 2026}
