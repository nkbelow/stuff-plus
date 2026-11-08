"""`stuff` command-line entry point.

uv run stuff ingest backfill --season 2024
uv run stuff ingest backfill --start 2024-04-01 --end 2024-04-30
uv run stuff ingest status
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from stuffplus.config import DEFAULT_DATA_CONFIG, date_range, load_data_config
from stuffplus.ingest.bronze import backfill as run_backfill
from stuffplus.ingest.bronze import season_summary
from stuffplus.ingest.savant import SavantClient

app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True, help="Bronze-layer Statcast ingestion.")
app.add_typer(ingest_app, name="ingest")

ConfigOpt = Annotated[Path, typer.Option("--config", help="Data config YAML.")]


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


@ingest_app.command()
def backfill(
    season: Annotated[
        list[int] | None, typer.Option(help="Season(s) to fetch, using configured windows.")
    ] = None,
    start: Annotated[
        dt.date | None,
        typer.Option(parser=_parse_date, metavar="YYYY-MM-DD", help="First date (YYYY-MM-DD)."),
    ] = None,
    end: Annotated[
        dt.date | None,
        typer.Option(parser=_parse_date, metavar="YYYY-MM-DD", help="Last date, inclusive."),
    ] = None,
    force: Annotated[bool, typer.Option(help="Re-fetch dates that are already complete.")] = False,
    config: ConfigOpt = DEFAULT_DATA_CONFIG,
) -> None:
    """Fetch game dates into bronze. Already-complete dates are skipped."""
    cfg = load_data_config(config)
    if bool(season) == (start is not None):
        raise typer.BadParameter("pass either --season or --start/--end")
    if season:
        dates = [d for s in season for d in cfg.season_dates(s)]
    else:
        dates = date_range(start, end or start)

    with SavantClient(cfg.savant) as client:
        summary = run_backfill(client, cfg.bronze_dir, dates, force=force)

    typer.echo(
        f"Fetched {len(summary.fetched)} dates ({summary.rows:,} rows), "
        f"skipped {len(summary.skipped)} complete, failed {len(summary.failed)}."
    )
    if summary.failed:
        for d, err in summary.failed.items():
            typer.echo(f"  FAILED {d}: {err}", err=True)
        raise typer.Exit(code=1)


@ingest_app.command()
def status(config: ConfigOpt = DEFAULT_DATA_CONFIG) -> None:
    """Per-season summary of what's in bronze, including schema drift."""
    cfg = load_data_config(config)
    summary = season_summary(cfg.bronze_dir)
    if summary is None:
        typer.echo(f"No bronze partitions under {cfg.bronze_dir}")
        return
    summary.show()
