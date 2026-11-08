---
status: accepted
date: 2026-11-07
---

# Separate repo from `fantasy_baseball`

`~/fantasy_baseball` already ingests Statcast leaderboards, so merging the two looks natural.
They stay separate because their lifecycles differ: fantasy is a daily operational pipeline,
while this is a research project with experiments, model artifacts and notebooks, about
3.6M pitch rows, and heavy ML dependencies. Both share conventions (uv, ruff, pytest,
DuckDB/Parquet), though this repo runs Python 3.14. Fantasy consumes this project's score
tables as Parquet when it needs them.
