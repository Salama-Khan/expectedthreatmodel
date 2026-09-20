"""Flatten a StatsBomb events.json file into the events table shape.

This module performs the Polars transform only. It does not insert into Postgres.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import polars as pl

# Column order and dtypes matching CREATE TABLE events.
EVENTS_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Utf8,
    "run_id": pl.Utf8,
    "match_id": pl.Int64,
    "team_id": pl.Int64,
    "event_index": pl.Int64,
    "possession_id": pl.Int64,
    "period": pl.Int64,
    "minute": pl.Int64,
    "second": pl.Float64,
    "type_name": pl.Utf8,
    "outcome": pl.Utf8,
    "actor_player_id": pl.Int64,
    "recipient_player_id": pl.Int64,
    "location_x": pl.Float64,
    "location_y": pl.Float64,
    "end_location_x": pl.Float64,
    "end_location_y": pl.Float64,
}

# Nested StatsBomb objects that may carry an outcome.name.
_OUTCOME_ROOTS: tuple[str, ...] = (
    "pass",
    "shot",
    "dribble",
    "duel",
    "goalkeeper",
    "interception",
    "ball_receipt",
    "substitution",
    "50_50",
    "block",
    "clearance",
)

# Nested objects that may carry end_location [x, y, (z)].
_END_LOCATION_ROOTS: tuple[str, ...] = (
    "pass",
    "carry",
    "shot",
    "goalkeeper",
)


def _struct_schema(dtype: pl.DataType) -> pl.Schema | None:
    if isinstance(dtype, pl.Struct):
        return dtype.to_schema()
    return None


def _navigate(
    schema: pl.Schema, column: str, *path: str
) -> tuple[pl.Expr, pl.DataType] | None:
    """Walk a nested struct path. Returns None if any segment is absent from schema."""
    if column not in schema:
        return None
    dtype: pl.DataType = schema[column]
    expr: pl.Expr = pl.col(column)
    for name in path:
        fields = _struct_schema(dtype)
        if fields is None or name not in fields:
            return None
        expr = expr.struct.field(name)
        dtype = fields[name]
    return expr, dtype


def _null(dtype: pl.DataType) -> pl.Expr:
    return pl.lit(None, dtype=dtype)


def _cast(nav: tuple[pl.Expr, pl.DataType] | None, dtype: pl.DataType) -> pl.Expr:
    if nav is None:
        return _null(dtype)
    return nav[0].cast(dtype, strict=False)


def _list_get(
    schema: pl.Schema,
    column: str,
    *path: str,
    index: int,
    dtype: pl.DataType,
) -> pl.Expr:
    nav = _navigate(schema, column, *path)
    if nav is None or not isinstance(nav[1], pl.List):
        return _null(dtype)
    return nav[0].list.get(index, null_on_oob=True).cast(dtype, strict=False)


def _coalesce(exprs: Sequence[pl.Expr], dtype: pl.DataType) -> pl.Expr:
    if not exprs:
        return _null(dtype)
    if len(exprs) == 1:
        return exprs[0]
    return pl.coalesce(list(exprs)).cast(dtype, strict=False)


def _infer_match_id(events_path: Path, match_id: int | None) -> int:
    if match_id is not None:
        return match_id
    stem = events_path.stem
    if not stem.isdigit():
        raise ValueError(
            "match_id is required when the events filename is not a numeric match id"
        )
    return int(stem)


def _second_expr(schema: pl.Schema) -> pl.Expr:
    """Keep sub-second precision.

    StatsBomb's `second` field is an integer. The fractional clock lives on
    `timestamp` (e.g. "00:00:00.575" -> 0.575).
    """
    timestamp_second = _null(pl.Float64)
    if "timestamp" in schema:
        timestamp_second = (
            pl.col("timestamp")
            .cast(pl.Utf8, strict=False)
            .str.split(":")
            .list.get(2)
            .cast(pl.Float64, strict=False)
        )

    integer_second = _cast(_navigate(schema, "second"), pl.Float64)
    return pl.coalesce(timestamp_second, integer_second).cast(pl.Float64)


def transform_statsbomb_events(
    events_path: str | Path,
    *,
    match_id: int | None = None,
    run_id: str | None = None,
) -> pl.DataFrame:
    """Load events.json and return a flat DataFrame matching the events table.

    Parameters
    ----------
    events_path:
        Path to a StatsBomb Open Data events JSON array.
    match_id:
        Match identifier. Inferred from a numeric filename stem when omitted.
    run_id:
        Ingestion-run UUID as text. Stored on every row; insertion is out of scope.
    """
    path = Path(events_path)
    resolved_match_id = _infer_match_id(path, match_id)

    # Scan every row so optional type-specific structs (pass/carry/shot/...)
    # are present in the schema even if they first appear late in the file.
    raw = pl.read_json(path, infer_schema_length=None)
    schema = raw.schema

    outcome_exprs = [
        _cast(_navigate(schema, root, "outcome", "name"), pl.Utf8)
        for root in _OUTCOME_ROOTS
    ]
    end_x_exprs = [
        _list_get(schema, root, "end_location", index=0, dtype=pl.Float64)
        for root in _END_LOCATION_ROOTS
    ]
    end_y_exprs = [
        _list_get(schema, root, "end_location", index=1, dtype=pl.Float64)
        for root in _END_LOCATION_ROOTS
    ]

    # Sort by StatsBomb's 1-based index so a 0-based event_index is deterministic.
    if "index" in schema:
        raw = raw.sort("index")

    events = raw.select(
        _cast(_navigate(schema, "id"), pl.Utf8).alias("event_id"),
        pl.lit(run_id, dtype=pl.Utf8).alias("run_id"),
        pl.lit(resolved_match_id, dtype=pl.Int64).alias("match_id"),
        _cast(_navigate(schema, "team", "id"), pl.Int64).alias("team_id"),
        pl.int_range(0, pl.len(), dtype=pl.Int64).alias("event_index"),
        _cast(_navigate(schema, "possession"), pl.Int64).alias("possession_id"),
        _cast(_navigate(schema, "period"), pl.Int64).alias("period"),
        _cast(_navigate(schema, "minute"), pl.Int64).alias("minute"),
        _second_expr(schema).alias("second"),
        _cast(_navigate(schema, "type", "name"), pl.Utf8).alias("type_name"),
        _coalesce(outcome_exprs, pl.Utf8).alias("outcome"),
        _cast(_navigate(schema, "player", "id"), pl.Int64).alias("actor_player_id"),
        # Implicitly successful StatsBomb passes omit pass.outcome. The events
        # CHECK then requires recipient + end coordinates, which live on `pass`.
        _cast(_navigate(schema, "pass", "recipient", "id"), pl.Int64).alias(
            "recipient_player_id"
        ),
        _list_get(schema, "location", index=0, dtype=pl.Float64).alias("location_x"),
        _list_get(schema, "location", index=1, dtype=pl.Float64).alias("location_y"),
        _coalesce(end_x_exprs, pl.Float64).alias("end_location_x"),
        _coalesce(end_y_exprs, pl.Float64).alias("end_location_y"),
    )

    return events.select(
        pl.col(name).cast(dtype, strict=False).alias(name)
        for name, dtype in EVENTS_SCHEMA.items()
    )
