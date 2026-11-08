import datetime as dt
import gzip
import json

import httpx

from stuffplus.ingest.bronze import (
    DATA_FILE,
    META_FILE,
    backfill,
    is_complete,
    manifest,
    partition_dir,
    season_summary,
)
from tests.conftest import GAME_DATE, pitch, savant_csv

NEXT_DAY = GAME_DATE + dt.timedelta(days=1)


def ok(rows: int = 2, game_date: dt.date = GAME_DATE) -> httpx.Response:
    pitches = [pitch(i, game_date=game_date.isoformat()) for i in range(1, rows + 1)]
    return httpx.Response(200, content=savant_csv(pitches))


def test_partition_layout(tmp_path):
    assert partition_dir(tmp_path, GAME_DATE) == tmp_path / "season=2024" / "game_date=2024-04-12"


def test_backfill_writes_raw_bytes_and_meta(tmp_path, make_client):
    body = savant_csv([pitch(1), pitch(2)])
    client = _client(make_client, [httpx.Response(200, content=body)])
    summary = backfill(client, tmp_path, [GAME_DATE])

    part = partition_dir(tmp_path, GAME_DATE)
    assert summary.fetched == [GAME_DATE]
    assert gzip.decompress((part / DATA_FILE).read_bytes()) == body
    meta = json.loads((part / META_FILE).read_text())
    assert meta["status"] == "ok"
    assert meta["rows"] == 2
    assert meta["season"] == 2024
    assert is_complete(part)
    assert not list(part.glob(".*.tmp"))


def test_rerun_skips_complete_dates(tmp_path, make_client):
    backfill(_client(make_client, [ok()]), tmp_path, [GAME_DATE])
    client = _client(make_client, [])  # any request would raise IndexError
    summary = backfill(client, tmp_path, [GAME_DATE])
    assert summary.skipped == [GAME_DATE]
    assert summary.fetched == []


def test_force_refetches_and_identical_response_gives_identical_file(tmp_path, make_client):
    backfill(_client(make_client, [ok()]), tmp_path, [GAME_DATE])
    data = partition_dir(tmp_path, GAME_DATE) / DATA_FILE
    first = data.read_bytes()
    summary = backfill(_client(make_client, [ok()]), tmp_path, [GAME_DATE], force=True)
    assert summary.fetched == [GAME_DATE]
    assert data.read_bytes() == first


def test_off_day_is_complete_with_zero_rows(tmp_path, make_client):
    backfill(_client(make_client, [ok(rows=0)]), tmp_path, [GAME_DATE])
    part = partition_dir(tmp_path, GAME_DATE)
    assert is_complete(part)
    assert json.loads((part / META_FILE).read_text())["rows"] == 0


def test_failed_date_leaves_no_partition_and_others_continue(tmp_path, make_client):
    bad = httpx.Response(200, content=savant_csv([pitch(1, game_date="1999-01-01")]))
    summary = backfill(
        _client(make_client, [bad, ok(game_date=NEXT_DAY)]), tmp_path, [GAME_DATE, NEXT_DAY]
    )
    assert list(summary.failed) == [GAME_DATE]
    assert summary.fetched == [NEXT_DAY]
    assert not is_complete(partition_dir(tmp_path, GAME_DATE))


def test_data_without_meta_is_incomplete(tmp_path, make_client):
    """Simulates a crash after the data write but before the meta write."""
    backfill(_client(make_client, [ok()]), tmp_path, [GAME_DATE])
    part = partition_dir(tmp_path, GAME_DATE)
    (part / META_FILE).unlink()
    assert not is_complete(part)
    assert backfill(_client(make_client, [ok()]), tmp_path, [GAME_DATE]).fetched == [GAME_DATE]


def test_manifest_reads_all_partitions(tmp_path, make_client):
    assert manifest(tmp_path) is None
    backfill(_client(make_client, [ok(rows=3), ok(rows=0)]), tmp_path, [GAME_DATE, NEXT_DAY])
    rel = manifest(tmp_path)
    assert rel is not None
    summed = rel.query("m", "SELECT sum(rows), count(*) FROM m")
    assert summed is not None
    assert summed.fetchall()[0] == (3, 2)


def test_season_summary_counts_game_days_and_schemas(tmp_path, make_client):
    assert season_summary(tmp_path) is None
    responses = [ok(rows=3), ok(rows=0, game_date=NEXT_DAY)]
    backfill(_client(make_client, responses), tmp_path, [GAME_DATE, NEXT_DAY])
    summary = season_summary(tmp_path)
    assert summary is not None
    row = summary.fetchall()[0]
    season, dates, game_days, pitches, dupes, schemas, first_game, last_game = row
    assert (season, dates, game_days, pitches, dupes, schemas) == (2024, 2, 1, 3, 0, 1)
    assert first_game == last_game == GAME_DATE


def _client(make_client, responses):
    client, _ = make_client(responses, max_retries=0)
    return client
