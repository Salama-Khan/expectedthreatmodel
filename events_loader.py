"""I bulk-load flattened StatsBomb events into PostgreSQL with run lineage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import polars as pl
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from events_transform import EVENTS_SCHEMA, partition_off_pitch

logger = logging.getLogger(__name__)

EVENTS_COLUMNS: tuple[str, ...] = tuple(EVENTS_SCHEMA)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """I return the committed lineage for a successful events load."""

    run_id: UUID
    inserted_count: int
    quarantined_count: int = 0
    status: str = "completed"


class EventsLoader:
    """I insert a flattened events DataFrame in an atomic COPY, keyed by ingestion_runs."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn


    def load(
        self,
        events: pl.DataFrame,
        *,
        source_uri: str,
        source_hash: str,
        provider_schema_version: str,
        transformation_version: str,
    ) -> IngestResult:
        """I persist events under a new ingestion run and return the completed result.

        I commit the `running` row first so I can still record `failed` after a COPY
        rollback. PostgreSQL will not accept further statements in an aborted
        transaction, so I cannot mark failure inside the same block that raised.
        """
        # I stamp lineage before I touch events so a crash cannot lose the run.
        with self._conn.transaction():
            run_id = self._start_run(
                source_uri=source_uri,
                source_hash=source_hash,
                provider_schema_version=provider_schema_version,
                transformation_version=transformation_version,
            )

        prepared = self._prepare_events(events, run_id)
        kept, rejected = partition_off_pitch(prepared)
        inserted_count = kept.height
        quarantined_count = rejected.height

        try:
            # I COPY events and mark the run completed in one strict transaction.
            # Off-pitch rows go to quarantined_events so they never fail the pitch CHECKs.
            with self._conn.transaction():
                self._copy_events(kept)
                self._copy_quarantine(rejected)
                self._mark_completed(run_id, inserted_count, quarantined_count)
        except psycopg.IntegrityError as exc:
            # I already rolled back the COPY via the transaction context.
            logger.exception(
                "I hit an integrity error while loading events for run_id=%s",
                run_id,
            )
            try:
                with self._conn.transaction():
                    self._mark_failed(run_id, exc)
            except Exception:
                logger.exception(
                    "I could not persist failed status for run_id=%s",
                    run_id,
                )
            raise

        logger.info(
            "I completed events load run_id=%s inserted_count=%s quarantined_count=%s",
            run_id,
            inserted_count,
            quarantined_count,
        )
        return IngestResult(
            run_id=run_id,
            inserted_count=inserted_count,
            quarantined_count=quarantined_count,
        )

    def _start_run(
        self,
        *,
        source_uri: str,
        source_hash: str,
        provider_schema_version: str,
        transformation_version: str,
    ) -> UUID:
        # I let Postgres generate run_id and I read it back immediately.
        row = self._conn.execute(
            """
            INSERT INTO ingestion_runs (
                source_uri,
                source_hash,
                provider_schema_version,
                transformation_version,
                status
            )
            VALUES (%s, %s, %s, %s, 'running')
            RETURNING run_id
            """,
            (
                source_uri,
                source_hash,
                provider_schema_version,
                transformation_version,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("I expected ingestion_runs RETURNING run_id to yield a row")
        run_id = row[0]
        if not isinstance(run_id, UUID):
            run_id = UUID(str(run_id))
        logger.info("I opened ingestion run_id=%s with status=running", run_id)
        return run_id

    def _prepare_events(self, events: pl.DataFrame, run_id: UUID) -> pl.DataFrame:
        missing = [name for name in EVENTS_COLUMNS if name != "run_id" and name not in events.columns]
        if missing:
            raise ValueError(f"I cannot load events; missing columns: {missing}")

        # I inject the generated run_id before COPY so every event row is lineage-keyed.
        prepared = events.with_columns(pl.lit(str(run_id), dtype=pl.Utf8).alias("run_id")).select(
            pl.col(name).cast(dtype, strict=False).alias(name)
            for name, dtype in EVENTS_SCHEMA.items()
        )
        # I turn NaN into SQL NULL so COPY does not send invalid float tokens.
        float_cols = [name for name, dtype in EVENTS_SCHEMA.items() if dtype == pl.Float64]
        if float_cols:
            prepared = prepared.with_columns(pl.col(float_cols).fill_nan(None))
        return prepared

    def _copy_events(self, events: pl.DataFrame) -> None:
        copy_sql = sql.SQL(
            "COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER false, NULL '', ENCODING 'UTF8')"
        ).format(
            sql.Identifier("events"),
            sql.SQL(", ").join(sql.Identifier(name) for name in EVENTS_COLUMNS),
        )
        payload = events.select(list(EVENTS_COLUMNS)).write_csv(
            include_header=False,
            null_value="",
        )
        with self._conn.cursor() as cur:
            with cur.copy(copy_sql) as copy:
                # I stream the CSV in chunks so a large match file never sits as one write.
                view = memoryview(payload.encode("utf-8"))
                chunk_size = 1 << 16
                for start in range(0, len(view), chunk_size):
                    copy.write(view[start : start + chunk_size])

    def _copy_quarantine(self, rejected: pl.DataFrame) -> None:
        if rejected.height == 0:
            return
        columns = (
            "run_id",
            "event_id",
            "match_id",
            "event_index",
            "reason",
            "location_x",
            "location_y",
            "end_location_x",
            "end_location_y",
        )
        copy_sql = sql.SQL(
            "COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER false, NULL '', ENCODING 'UTF8')"
        ).format(
            sql.Identifier("quarantined_events"),
            sql.SQL(", ").join(sql.Identifier(name) for name in columns),
        )
        payload = rejected.select(list(columns)).write_csv(
            include_header=False,
            null_value="",
        )
        with self._conn.cursor() as cur:
            with cur.copy(copy_sql) as copy:
                copy.write(payload.encode("utf-8"))

    def _mark_completed(
        self, run_id: UUID, inserted_count: int, quarantined_count: int
    ) -> None:
        # I set completed_at here because the table forbids completed without a timestamp.
        self._conn.execute(
            """
            UPDATE ingestion_runs
            SET
                status = 'completed',
                inserted_count = %s,
                quarantined_count = %s,
                completed_at = NOW(),
                error_log = NULL
            WHERE run_id = %s
            """,
            (inserted_count, quarantined_count, run_id),
        )

    def _mark_failed(self, run_id: UUID, exc: BaseException) -> None:
        # I store the Postgres diagnostic payload so I can debug CHECK/FK failures later.
        self._conn.execute(
            """
            UPDATE ingestion_runs
            SET
                status = 'failed',
                completed_at = NOW(),
                error_log = %s
            WHERE run_id = %s
            """,
            (Jsonb(_integrity_error_log(exc)), run_id),
        )


def _integrity_error_log(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    diag = getattr(exc, "diag", None)
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate is not None:
        payload["sqlstate"] = sqlstate
    if diag is not None:
        payload["diag"] = {
            "severity": diag.severity,
            "message_primary": diag.message_primary,
            "message_detail": diag.message_detail,
            "constraint_name": diag.constraint_name,
            "table_name": diag.table_name,
            "column_name": diag.column_name,
        }
    return payload


def load_events(
    conn: psycopg.Connection[Any],
    events: pl.DataFrame,
    *,
    source_uri: str,
    source_hash: str,
    provider_schema_version: str,
    transformation_version: str,
) -> IngestResult:
    """I load a flattened events DataFrame using EventsLoader."""
    return EventsLoader(conn).load(
        events,
        source_uri=source_uri,
        source_hash=source_hash,
        provider_schema_version=provider_schema_version,
        transformation_version=transformation_version,
    )
