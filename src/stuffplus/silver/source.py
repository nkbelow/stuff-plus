"""A season's bronze input, as the silver build and audit read it.

`BronzeSeason` holds the season's complete, non-empty partitions and each file's header.
`profile_bronze` measures the facts silver alone can't show (bronze row counts, exact
duplicates, deprecated column contents, game dates vs. partition dates), so the audit can
reconcile silver against an independent count.

Failure semantics:

  - `BronzeSeason.load` raises ValueError for a bronze file with no header line, and
    OSError or EOFError for an unreadable gzip. The queries raise duckdb.Error.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb

from stuffplus.ingest.bronze import Partition, complete_partitions, read_header
from stuffplus.silver.contract import DEPRECATED_COLUMNS
from stuffplus.sql import quote_literal

SQL_DIR = Path(__file__).parent / "sql"

# A hash of every bronze value in a row except the source filename. Two rows are exact
# duplicates only if this matches, so a difference in any column, kept or not, counts.
ROW_HASH = "md5(to_json(struct_pack(*COLUMNS(raw.* EXCLUDE (filename))))::VARCHAR)"


@dataclass(frozen=True)
class BronzeSeason:
    season: int
    partitions: tuple[Partition, ...]
    headers: dict[Path, tuple[str, ...]]  # data file -> its column names

    @classmethod
    def load(cls, bronze_dir: Path, season: int) -> BronzeSeason:
        # Off-days are header-only files: they add no rows, and leaving them out keeps
        # DuckDB's CSV sniffing on files that have data.
        partitions = tuple(p for p in complete_partitions(bronze_dir, season) if p.rows > 0)
        headers = {p.data_file: read_header(p.data_file) for p in partitions}
        return cls(season=season, partitions=partitions, headers=headers)

    @property
    def columns(self) -> set[str]:
        """Every column present in at least one of the season's files."""
        return {name for header in self.headers.values() for name in header}

    def missing(self, columns: Iterable[str]) -> dict[str, list[dt.date]]:
        """Each of `columns` absent from some file -> the partition dates it is absent on."""
        columns = list(columns)  # walked once per partition
        missing: dict[str, list[dt.date]] = {}
        for p in self.partitions:
            header = set(self.headers[p.data_file])
            for column in columns:
                if column not in header:
                    missing.setdefault(column, []).append(p.game_date)
        return missing

    @property
    def rows(self) -> int:
        """Rows recorded in the partitions' metadata at fetch time."""
        return sum(p.rows for p in self.partitions)

    def register(self, con: duckdb.DuckDBPyConnection) -> None:
        """Create the `lineage` table that `relation()` joins each row to."""
        con.execute(
            "CREATE OR REPLACE TEMP TABLE lineage "
            "(data_file VARCHAR, game_date DATE, fetched_at TIMESTAMPTZ)"
        )
        con.executemany(
            "INSERT INTO lineage VALUES (?, ?, ?)",
            [[str(p.data_file), p.game_date, p.fetched_at] for p in self.partitions],
        )

    def relation(self) -> str:
        """SQL FROM clause: every bronze row as VARCHAR (`raw`) joined to its `lineage`."""
        files = "[" + ", ".join(quote_literal(p.data_file) for p in self.partitions) + "]"
        return (
            f"read_csv({files}, all_varchar = true, header = true, union_by_name = true, "
            # Off: DuckDB would otherwise read `season` and `game_date` from the path
            # (season=YYYY/game_date=...), replacing the file's own game_date.
            "hive_partitioning = false, filename = true) "
            "AS raw JOIN lineage ON raw.filename = lineage.data_file"
        )


@dataclass(frozen=True)
class BronzeProfile:
    rows: int  # from the partitions' metadata, independent of any query
    rows_read: int  # read through the build's own join
    distinct_rows: int
    date_mismatches: int  # rows whose game_date differs from their partition's
    date_mismatch_examples: str | None
    deprecated_values: dict[str, int]  # non-empty values per deprecated column present

    @property
    def exact_duplicates(self) -> int:
        return self.rows_read - self.distinct_rows


def profile_bronze(con: duckdb.DuckDBPyConnection, bronze: BronzeSeason) -> BronzeProfile:
    """Measure the season's bronze rows. `bronze.register(con)` must have run."""
    deprecated = [c for c in DEPRECATED_COLUMNS if c in bronze.columns]
    sql = (
        (SQL_DIR / "bronze_profile.sql")
        .read_text()
        .format(
            row_hash=ROW_HASH,
            deprecated_counts="".join(f', count(raw."{c}")' for c in deprecated),
            source=bronze.relation(),
        )
    )
    rows_read, distinct_rows, mismatches, examples, *counts = con.execute(sql).fetchall()[0]
    return BronzeProfile(
        rows=bronze.rows,
        rows_read=rows_read,
        distinct_rows=distinct_rows,
        date_mismatches=mismatches,
        date_mismatch_examples=examples,
        deprecated_values=dict(zip(deprecated, counts, strict=True)),
    )
