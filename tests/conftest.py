import datetime as dt
from pathlib import Path

import httpx
import pytest

from stuffplus.config import DataConfig, SavantConfig, SeasonWindow
from stuffplus.ingest.bronze import write_partition
from stuffplus.ingest.savant import FetchResult, SavantClient, validate_csv
from stuffplus.silver.contract import SOURCE_COLUMNS

HEADER = [
    "pitch_type",
    "game_date",
    "release_speed",
    "game_type",
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "pitcher",
    "des",
]

GAME_DATE = dt.date(2024, 4, 12)


def savant_csv(rows: list[dict[str, str]], header: list[str] = HEADER) -> bytes:
    """Build a body shaped like Savant's: UTF-8 BOM, every field quoted."""
    lines = [",".join(f'"{h}"' for h in header)]
    for row in rows:
        lines.append(",".join(f'"{row.get(h, "")}"' for h in header))
    return ("﻿" + "\n".join(lines)).encode()


def pitch(n: int, **overrides: str) -> dict[str, str]:
    row = {
        "pitch_type": "FF",
        "game_date": GAME_DATE.isoformat(),
        "release_speed": "95.1",
        "game_type": "R",
        "game_pk": "745000",
        "at_bat_number": "1",
        "pitch_number": str(n),
        "pitcher": "543037",
        "des": "Strikeout, swinging, on a 1-2 count",
    }
    row.update(overrides)
    return row


class Responder:
    """Queue of responses for httpx.MockTransport; records requested URLs."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def make_client():
    sleeps: list[float] = []

    def _make(responses: list[httpx.Response | Exception], **cfg) -> tuple[SavantClient, Responder]:
        responder = Responder(responses)
        http = httpx.Client(transport=httpx.MockTransport(responder))
        client = SavantClient(SavantConfig(min_interval_s=0, **cfg), http=http, sleep=sleeps.append)
        client.sleeps = sleeps  # type: ignore[attr-defined]
        return client, responder

    return _make


SILVER_FIXTURE_HEADER = [c.name for c in SOURCE_COLUMNS] + ["des"]


def silver_pitch(n: int, game_date: dt.date = GAME_DATE, **overrides: str) -> dict[str, str]:
    """One bronze row carrying every starter schema contract column."""
    row = {
        "game_pk": "745000",
        "at_bat_number": "1",
        "pitch_number": str(n),
        "pitcher": "543037",
        "batter": "592450",
        "game_date": game_date.isoformat(),
        "game_type": "R",
        "home_team": "NYY",
        "away_team": "TOR",
        "p_throws": "R",
        "stand": "L",
        "balls": "0",
        "strikes": "1",
        "pitch_type": "FF",
        "description": "called_strike",
        "type": "S",
        "events": "",
        "release_speed": "95.1",
        "release_spin_rate": "2300",
        "spin_axis": "210",
        "pfx_x": "-0.55",
        "pfx_z": "1.30",
        "release_pos_x": "-1.9",
        "release_pos_z": "5.8",
        "release_extension": "6.5",
        "arm_angle": "44.6",
        "des": "",
    }
    row.update(overrides)
    return row


def write_bronze(
    bronze_dir: Path,
    game_date: dt.date,
    rows: list[dict[str, str]],
    fetched_at: dt.datetime = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC),
) -> None:
    """Write one complete bronze partition through the real bronze writer."""
    body = savant_csv(rows, header=SILVER_FIXTURE_HEADER)
    n_rows, columns, dupes = validate_csv(body, game_date)
    write_partition(
        bronze_dir,
        FetchResult(
            game_date=game_date,
            url="https://example.test",
            http_status=200,
            fetched_at=fetched_at,
            body=body,
            rows=n_rows,
            columns=columns,
            duplicate_keys=dupes,
        ),
    )


@pytest.fixture
def cfg(tmp_path: Path) -> DataConfig:
    """A data config rooted in a temp dir, for tests that build layers on disk."""
    return DataConfig(
        data_root=tmp_path,
        seasons={2024: SeasonWindow(start=dt.date(2024, 3, 15), end=dt.date(2024, 10, 6))},
    )
