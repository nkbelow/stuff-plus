"""Small helpers for building DuckDB SQL text."""


def quote_literal(value: object) -> str:
    """A SQL string literal, for paths in places DuckDB can't take a bound parameter."""
    return "'" + str(value).replace("'", "''") + "'"
