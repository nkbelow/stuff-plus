"""DuckDB catalog: named views over the data layers, holding no data itself.

`data/stuff.duckdb` plays the role a metastore plays in Databricks: it maps names like
`silver.pitches` to files. It is rebuilt from scratch on every refresh, so it can always be
deleted. Views hold absolute paths from `DataConfig`, so refresh after moving `data_root`.

A view is created only when its files exist: DuckDB binds a view's query at creation,
and a glob matching nothing fails to bind.

Failure semantics:

  - The catalog file is replaced atomically: a failed refresh leaves the previous one.
  - A view that can't bind (e.g. unreadable files) raises duckdb.Error.
"""

import glob
from pathlib import Path

import duckdb

from stuffplus.config import DataConfig
from stuffplus.fs import atomic_path
from stuffplus.silver.layout import SilverLayout
from stuffplus.sql import quote_literal


def refresh_catalog(cfg: DataConfig) -> list[str]:
    """Recreate the catalog. Returns the names of the views it now holds."""
    bronze = cfg.bronze_dir / "season=*" / "game_date=*"
    silver = SilverLayout(cfg.silver_dir)
    # name -> (file glob, table function reading it)
    sources: dict[str, tuple[Path, str]] = {
        # No union_by_name: binding it inspects every one of ~1,000 files and exhausted
        # 12.7 GB of memory. The 2022–2026 header audit (#2) found one identical header in
        # every partition; silver's schema contract check fails a season that breaks it.
        "bronze.statcast": (
            bronze / "part.csv.gz",
            "read_csv({}, all_varchar = true, header = true, hive_partitioning = true)",
        ),
        "bronze.manifest": (bronze / "_meta.json", "read_json_auto({})"),
        "silver.pitches": (silver.pitches("*"), "read_parquet({}, hive_partitioning = true)"),
        **{
            # The files carry their own season column.
            f"silver.{report}": (
                silver.dq(report, "*"),
                "read_parquet({}, hive_partitioning = false)",
            )
            for report in ("dq_results", "dq_null_rates", "dq_games")
        },
    }
    views = {
        name: reader.format(quote_literal(pattern))
        for name, (pattern, reader) in sources.items()
        if glob.glob(str(pattern))
    }

    with atomic_path(cfg.catalog_path) as tmp, duckdb.connect(str(tmp)) as con:
        con.execute("CREATE SCHEMA bronze; CREATE SCHEMA silver;")
        for name, query in views.items():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM {query}")
    return sorted(views)
