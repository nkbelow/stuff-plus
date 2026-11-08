from __future__ import annotations

import datetime as dt

import httpx
import pytest

from stuffplus.config import SavantConfig
from stuffplus.ingest.savant import SavantClient

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
