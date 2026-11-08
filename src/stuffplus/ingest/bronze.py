"""Bronze layer: raw Savant CSVs on disk, one partition per game date.

Layout:

  <bronze_dir>/season=YYYY/game_date=YYYY-MM-DD/part.csv.gz   exact response bytes, gzipped
  <bronze_dir>/season=YYYY/game_date=YYYY-MM-DD/_meta.json     fetch metadata

`_meta.json` is written last, so its presence means the partition is complete.
A crash mid-write leaves no meta and the date is fetched again next run.
The manifest is just a DuckDB view over all `_meta.json` files (see `manifest`).

Re-runs are idempotent: complete partitions are skipped unless `force=True`, and
gzip is written with mtime=0 so identical responses produce identical files.
"""

import csv
import datetime as dt
import gzip
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from stuffplus.fs import atomic_path
from stuffplus.ingest.savant import FETCHER_VERSION, FetchResult, SavantClient, SavantError

DATA_FILE = "part.csv.gz"
META_FILE = "_meta.json"


def partition_dir(bronze_dir: Path, game_date: dt.date) -> Path:
    return bronze_dir / f"season={game_date.year}" / f"game_date={game_date.isoformat()}"


def is_complete(part_dir: Path) -> bool:
    return _complete_meta(part_dir) is not None


def _complete_meta(part_dir: Path) -> dict[str, Any] | None:
    """The partition's metadata if it is complete, else None."""
    meta = part_dir / META_FILE
    if not meta.exists() or not (part_dir / DATA_FILE).exists():
        return None
    parsed: dict[str, Any] = json.loads(meta.read_text())
    return parsed if parsed.get("status") == "ok" else None


@dataclass(frozen=True)
class Partition:
    """One complete game-date partition, as downstream layers see it."""

    game_date: dt.date
    data_file: Path
    rows: int
    fetched_at: dt.datetime


def complete_partitions(bronze_dir: Path, season: int) -> list[Partition]:
    """A season's complete partitions in date order. Incomplete ones are skipped."""
    partitions = []
    for part_dir in sorted((bronze_dir / f"season={season}").glob("game_date=*")):
        meta = _complete_meta(part_dir)
        if meta is None:
            continue
        partitions.append(
            Partition(
                game_date=dt.date.fromisoformat(meta["game_date"]),
                data_file=part_dir / DATA_FILE,
                rows=meta["rows"],
                fetched_at=dt.datetime.fromisoformat(meta["fetched_at"]),
            )
        )
    return partitions


def read_header(data_file: Path) -> tuple[str, ...]:
    """A bronze file's column names, in order. Savant prefixes a UTF-8 BOM."""
    with gzip.open(data_file, "rt", encoding="utf-8-sig", newline="") as f:
        return tuple(next(csv.reader(f), []))


def seasons(bronze_dir: Path) -> list[int]:
    """Seasons with at least one bronze partition directory."""
    return sorted(int(p.name.removeprefix("season=")) for p in bronze_dir.glob("season=*"))


def _atomic_write(path: Path, data: bytes) -> None:
    with atomic_path(path) as tmp, open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_partition(bronze_dir: Path, result: FetchResult) -> dict[str, Any]:
    """Persist one fetch. Returns the metadata written."""
    part_dir = partition_dir(bronze_dir, result.game_date)
    part_dir.mkdir(parents=True, exist_ok=True)
    # Drop the old meta first so a crash between the two writes can't leave a
    # meta describing a different data file.
    (part_dir / META_FILE).unlink(missing_ok=True)
    _atomic_write(part_dir / DATA_FILE, gzip.compress(result.body, mtime=0))
    meta = {
        "status": "ok",
        "game_date": result.game_date.isoformat(),
        "season": result.game_date.year,
        "rows": result.rows,
        "duplicate_keys": result.duplicate_keys,
        "n_columns": len(result.columns),
        "columns_sha256": result.columns_sha256,
        "body_sha256": result.body_sha256,
        "body_bytes": len(result.body),
        "http_status": result.http_status,
        "url": result.url,
        "fetched_at": result.fetched_at.isoformat(),
        "fetcher_version": FETCHER_VERSION,
    }
    _atomic_write(part_dir / META_FILE, json.dumps(meta, indent=2).encode())
    return meta


@dataclass
class BackfillSummary:
    fetched: list[dt.date] = field(default_factory=list)
    skipped: list[dt.date] = field(default_factory=list)
    failed: dict[dt.date, str] = field(default_factory=dict)
    rows: int = 0


def backfill(
    client: SavantClient,
    bronze_dir: Path,
    dates: Iterable[dt.date],
    force: bool = False,
) -> BackfillSummary:
    """Fetch every date not already complete. One date failing doesn't stop the rest."""
    summary = BackfillSummary()
    for game_date in dates:
        if not force and is_complete(partition_dir(bronze_dir, game_date)):
            summary.skipped.append(game_date)
            continue
        try:
            result = client.fetch_date(game_date)
        except SavantError as exc:
            print(f"[error] {game_date}: {exc}")
            summary.failed[game_date] = str(exc)
            continue
        write_partition(bronze_dir, result)
        summary.fetched.append(game_date)
        summary.rows += result.rows
        dupes = f", {result.duplicate_keys} duplicate keys" if result.duplicate_keys else ""
        print(f"{game_date}: {result.rows} rows{dupes}")
    return summary


def manifest(bronze_dir: Path) -> duckdb.DuckDBPyRelation | None:
    """All partition metadata as a DuckDB relation, or None if nothing is ingested yet."""
    if not any(bronze_dir.glob(f"season=*/game_date=*/{META_FILE}")):
        return None
    pattern = str(bronze_dir / "season=*" / "game_date=*" / META_FILE)
    return duckdb.sql(f"SELECT * FROM read_json_auto('{pattern}')")


def season_summary(bronze_dir: Path) -> duckdb.DuckDBPyRelation | None:
    """Per-season counts from the manifest; `schemas` > 1 means Savant's columns drifted."""
    rel = manifest(bronze_dir)
    if rel is None:
        return None
    return rel.query(
        "m",
        """
        SELECT season,
               count(*)                                AS dates,
               count(*) FILTER (WHERE rows > 0)        AS game_days,
               sum(rows)                               AS pitches,
               sum(duplicate_keys)                     AS duplicate_keys,
               count(DISTINCT columns_sha256)          AS schemas,
               min(game_date) FILTER (WHERE rows > 0)  AS first_game,
               max(game_date) FILTER (WHERE rows > 0)  AS last_game
        FROM m
        GROUP BY season
        ORDER BY season
        """,
    )
