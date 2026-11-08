"""Fetch one game date of pitch-level Statcast data from Baseball Savant's CSV export.

The endpoint is the one behind Savant's "Statcast Search" download button (the same
one pybaseball wraps). It is undocumented, so everything we rely on is validated on
every response:

  - Body is a CSV whose header contains REQUIRED_COLUMNS (an HTML error page or an
    `error` column means Savant failed, even with HTTP 200).
  - Every row's game_date equals the requested date and game_type is 'R'.
  - Row count stays under Savant's per-query cap; hitting it means silent truncation.

Failure semantics:

  - Network errors, 429, 5xx, and non-CSV bodies -> SavantTransientError, retried
    with exponential backoff (honoring Retry-After).
  - Anything else wrong with a well-formed CSV -> SavantError, not retried: retrying
    a deterministic mismatch just burns requests.
"""

import csv
import datetime as dt
import hashlib
import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from stuffplus.config import SavantConfig

FETCHER_VERSION = "1"

# Savant caps a single search at 25k rows. A full MLB day is ~4.5k, so reaching
# the cap means the response was truncated, not that it was a big day.
ROW_CAP = 25_000

REQUIRED_COLUMNS = (
    "pitch_type",
    "game_date",
    "game_type",
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "pitcher",
)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SavantError(Exception):
    """Response was received but is wrong in a way retrying won't fix."""


class SavantTransientError(SavantError):
    """Network failure, throttling, server error, or a non-CSV body. Worth retrying."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class FetchResult:
    game_date: dt.date
    url: str
    http_status: int
    fetched_at: dt.datetime
    body: bytes
    rows: int
    columns: list[str]
    duplicate_keys: int

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def columns_sha256(self) -> str:
        return hashlib.sha256(",".join(self.columns).encode()).hexdigest()


def build_url(base_url: str, game_date: dt.date) -> str:
    params = {
        "all": "true",
        "type": "details",
        "player_type": "pitcher",
        "hfGT": "R|",  # regular season only
        "game_date_gt": game_date.isoformat(),
        "game_date_lt": game_date.isoformat(),  # both bounds inclusive
        "min_pitches": "0",
        "min_results": "0",
        "group_by": "name",
        "sort_col": "pitches",
        "sort_order": "desc",
    }
    return f"{base_url}?{urlencode(params)}"


def validate_csv(body: bytes, game_date: dt.date) -> tuple[int, list[str], int]:
    """Check a Savant CSV body for one date. Returns (rows, columns, duplicate_keys)."""
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SavantTransientError(f"body is not UTF-8: {exc}") from exc

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or "error" in header:
        raise SavantTransientError(f"unexpected body (no CSV header or error): {text[:200]!r}")
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SavantTransientError(f"CSV header missing required columns {missing}")

    idx = {name: i for i, name in enumerate(header)}
    want_date = game_date.isoformat()
    rows = 0
    keys: set[tuple[str, str, str]] = set()
    for line_no, row in enumerate(reader, start=2):
        if len(row) != len(header):
            raise SavantError(f"line {line_no}: {len(row)} fields, header has {len(header)}")
        if row[idx["game_date"]] != want_date:
            raise SavantError(f"line {line_no}: game_date {row[idx['game_date']]} != {want_date}")
        if row[idx["game_type"]] != "R":
            raise SavantError(f"line {line_no}: game_type {row[idx['game_type']]!r} != 'R'")
        keys.add((row[idx["game_pk"]], row[idx["at_bat_number"]], row[idx["pitch_number"]]))
        rows += 1

    if rows >= ROW_CAP:
        raise SavantError(f"{rows} rows hits Savant's {ROW_CAP} cap; response is truncated")
    return rows, header, rows - len(keys)


class SavantClient:
    """Rate-limited, retrying fetcher for single game dates.

    `sleep` and `clock` are injectable so tests run instantly.
    """

    def __init__(
        self,
        cfg: SavantConfig,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._http = http or httpx.Client(
            timeout=cfg.timeout_s,
            headers={"User-Agent": cfg.user_agent},
            follow_redirects=True,
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SavantClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_date(self, game_date: dt.date) -> FetchResult:
        url = build_url(self._cfg.base_url, game_date)
        attempt = 0
        while True:
            try:
                return self._fetch_once(url, game_date)
            except SavantTransientError as exc:
                attempt += 1
                if attempt > self._cfg.max_retries:
                    raise SavantError(
                        f"{game_date}: giving up after {self._cfg.max_retries} retries: {exc}"
                    ) from exc
                delay = exc.retry_after or self._cfg.backoff_s * 2 ** (attempt - 1)
                print(f"[retry {attempt}/{self._cfg.max_retries}] {game_date}: {exc}; {delay:.0f}s")
                self._sleep(delay)

    def _fetch_once(self, url: str, game_date: dt.date) -> FetchResult:
        self._throttle()
        try:
            resp = self._http.get(url)
        except httpx.TransportError as exc:
            raise SavantTransientError(f"{type(exc).__name__}: {exc}") from exc

        if resp.status_code in _RETRYABLE_STATUS:
            retry_after = resp.headers.get("Retry-After", "")
            raise SavantTransientError(
                f"HTTP {resp.status_code}",
                retry_after=float(retry_after) if retry_after.isdigit() else None,
            )
        if resp.status_code != 200:
            raise SavantError(f"HTTP {resp.status_code}: {resp.text[:200]!r}")

        rows, columns, dupes = validate_csv(resp.content, game_date)
        return FetchResult(
            game_date=game_date,
            url=url,
            http_status=resp.status_code,
            fetched_at=dt.datetime.now(dt.UTC),
            body=resp.content,
            rows=rows,
            columns=columns,
            duplicate_keys=dupes,
        )

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._cfg.min_interval_s - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()
