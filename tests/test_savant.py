import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from conftest import GAME_DATE, HEADER, pitch, savant_csv

from stuffplus.ingest import savant
from stuffplus.ingest.savant import SavantError, SavantTransientError, build_url, validate_csv


def test_build_url_requests_single_regular_season_date():
    qs = parse_qs(urlparse(build_url("https://x/csv", GAME_DATE)).query)
    assert qs["game_date_gt"] == qs["game_date_lt"] == ["2024-04-12"]
    assert qs["hfGT"] == ["R|"]
    assert qs["type"] == ["details"]


def test_validate_counts_rows_and_strips_bom():
    rows, columns, dupes = validate_csv(savant_csv([pitch(1), pitch(2)]), GAME_DATE)
    assert rows == 2
    assert columns == HEADER  # BOM removed from first column name
    assert dupes == 0


def test_validate_header_only_is_a_valid_off_day():
    assert validate_csv(savant_csv([]), GAME_DATE) == (0, HEADER, 0)


def test_validate_counts_duplicate_pitch_keys():
    _, _, dupes = validate_csv(savant_csv([pitch(1), pitch(1), pitch(2)]), GAME_DATE)
    assert dupes == 1


def test_validate_handles_embedded_newlines_in_quoted_fields():
    rows, _, _ = validate_csv(savant_csv([pitch(1, des="line one\nline two")]), GAME_DATE)
    assert rows == 1


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Service Unavailable</body></html>",
        b'"error"\n"Query timed out"',
        savant_csv([], header=["pitch_type", "game_date"]),  # required columns missing
        b"",
    ],
)
def test_validate_non_csv_bodies_are_transient(body):
    with pytest.raises(SavantTransientError):
        validate_csv(body, GAME_DATE)


@pytest.mark.parametrize(
    "row",
    [pitch(1, game_date="2024-04-13"), pitch(1, game_type="S")],
    ids=["wrong_date", "spring_training"],
)
def test_validate_wrong_rows_are_permanent_errors(row):
    with pytest.raises(SavantError) as exc_info:
        validate_csv(savant_csv([row]), GAME_DATE)
    assert not isinstance(exc_info.value, SavantTransientError)


def test_validate_rejects_truncated_response(monkeypatch):
    monkeypatch.setattr(savant, "ROW_CAP", 3)
    with pytest.raises(SavantError, match="cap"):
        validate_csv(savant_csv([pitch(i) for i in range(3)]), GAME_DATE)


def test_fetch_success(make_client):
    body = savant_csv([pitch(1), pitch(2)])
    client, responder = make_client([httpx.Response(200, content=body)])
    result = client.fetch_date(GAME_DATE)
    assert result.rows == 2
    assert result.body == body
    assert result.http_status == 200
    assert len(responder.urls) == 1


def test_fetch_retries_transient_failures_with_backoff(make_client):
    client, responder = make_client(
        [
            httpx.ConnectError("boom"),
            httpx.Response(503),
            httpx.Response(200, content=b"<html>oops</html>"),
            httpx.Response(200, content=savant_csv([pitch(1)])),
        ],
        backoff_s=2.0,
    )
    assert client.fetch_date(GAME_DATE).rows == 1
    assert len(responder.urls) == 4
    assert client.sleeps == [2.0, 4.0, 8.0]


def test_fetch_honors_retry_after(make_client):
    client, _ = make_client(
        [
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, content=savant_csv([])),
        ]
    )
    client.fetch_date(GAME_DATE)
    assert client.sleeps == [30.0]


def test_fetch_gives_up_after_max_retries(make_client):
    client, responder = make_client([httpx.Response(500)] * 3, max_retries=2)
    with pytest.raises(SavantError, match="giving up"):
        client.fetch_date(GAME_DATE)
    assert len(responder.urls) == 3


def test_fetch_does_not_retry_permanent_errors(make_client):
    client, responder = make_client(
        [httpx.Response(200, content=savant_csv([pitch(1, game_date="2024-04-13")]))]
    )
    with pytest.raises(SavantError):
        client.fetch_date(GAME_DATE)
    assert len(responder.urls) == 1


def test_throttle_spaces_requests():
    times = iter([0.0, 0.0, 0.25, 1.0])
    sleeps: list[float] = []
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=savant_csv([])))
    )
    client = savant.SavantClient(
        savant.SavantConfig(min_interval_s=1.0),
        http=http,
        sleep=sleeps.append,
        clock=lambda: next(times),
    )
    client.fetch_date(GAME_DATE)
    client.fetch_date(GAME_DATE + dt.timedelta(days=1))
    assert sleeps == [0.75]
