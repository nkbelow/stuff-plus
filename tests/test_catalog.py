import duckdb

from stuffplus.catalog import refresh_catalog
from stuffplus.config import DataConfig
from stuffplus.silver.build import build_season
from tests.conftest import GAME_DATE, silver_pitch, write_bronze


def tables(cfg: DataConfig) -> list[tuple[str, str]]:
    with duckdb.connect(str(cfg.catalog_path), read_only=True) as con:
        rows = con.execute(
            "SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables "
            "ORDER BY 1"
        ).fetchall()
    return [(name, kind) for name, kind in rows]


def test_catalog_names_silver_and_bronze_as_views_only(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1), silver_pitch(2)])
    build_season(cfg, 2024)

    assert refresh_catalog(cfg) == [
        "bronze.manifest",
        "bronze.statcast",
        "silver.dq_results",
        "silver.pitches",
    ]
    assert {kind for _, kind in tables(cfg)} == {"VIEW"}
    with duckdb.connect(str(cfg.catalog_path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM silver.pitches").fetchall() == [(2,)]
        assert con.execute("SELECT season FROM silver.pitches LIMIT 1").fetchall() == [(2024,)]


def test_layers_without_files_get_no_view(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    assert refresh_catalog(cfg) == ["bronze.manifest", "bronze.statcast"]


def test_catalog_can_be_deleted_and_rebuilt(cfg):
    write_bronze(cfg.bronze_dir, GAME_DATE, [silver_pitch(1)])
    build_season(cfg, 2024)
    refresh_catalog(cfg)
    cfg.catalog_path.unlink()
    refresh_catalog(cfg)
    assert len(tables(cfg)) == 4
