"""DuckDB catalog: named views over the data layers, holding no data itself.

`data/stuff.duckdb` plays the role a metastore plays in Databricks: it maps names like
`silver.pitches` to files. It is rebuilt from scratch on every refresh (into a temp file,
then an atomic rename), so it can always be deleted. Views hold absolute paths from
`DataConfig`, so refresh after moving `data_root`.

A view is created only when its files exist: DuckDB binds a view's query at creation,
and a glob matching nothing fails to bind.
"""

import glob
import os

import duckdb

from stuffplus.config import DataConfig
from stuffplus.sql import quote_literal


def refresh_catalog(cfg: DataConfig) -> list[str]:
    """Recreate the catalog. Returns the names of the views it now holds."""
    bronze = cfg.bronze_dir / "season=*" / "game_date=*"
    silver = cfg.silver_dir
    # name -> (file glob, table function reading it)
    sources = {
        # No union_by_name: it inspects all ~1,000 files at bind time and exhausted memory.
        # Every bronze file has the same header, which the schema contract enforces.
        "bronze.statcast": (
            bronze / "part.csv.gz",
            "read_csv({}, all_varchar = true, header = true, hive_partitioning = true)",
        ),
        "bronze.manifest": (bronze / "_meta.json", "read_json_auto({})"),
        "silver.pitches": (
            silver / "pitches" / "season=*" / "part.parquet",
            "read_parquet({}, hive_partitioning = true)",
        ),
        "silver.dq_results": (
            silver / "dq" / "dq_results" / "season=*" / "part.parquet",
            "read_parquet({}, hive_partitioning = true)",
        ),
    }
    views = {
        name: reader.format(quote_literal(pattern))
        for name, (pattern, reader) in sources.items()
        if glob.glob(str(pattern))
    }

    path = cfg.catalog_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    with duckdb.connect(str(tmp)) as con:
        con.execute("CREATE SCHEMA bronze; CREATE SCHEMA silver;")
        for name, query in views.items():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM {query}")
    os.replace(tmp, path)
    return sorted(views)
