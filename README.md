# stuff-plus

A personal, learning-focused Stuff+-style pitch quality model built from public
Statcast data.

This is **not** FanGraphs, Driveline, or any other proprietary Stuff+. It is an
independent metric with its own documented target, features and scaling.

## Setup

```bash
uv sync
uv run stuff ingest backfill --start 2024-04-01 --end 2024-04-30
uv run stuff ingest status
uv run pytest
```
