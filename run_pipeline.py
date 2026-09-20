"""I run one StatsBomb events file through transform + Postgres load."""

from __future__ import annotations

import argparse
import hashlib
import logging
from pathlib import Path
from typing import Any, Mapping

import psycopg

from events_loader import load_events
from events_transform import transform_statsbomb_events
from seed_match_context import ensure_lineup_path, seed_match_context

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql://postgres@localhost:5432/footballanalysis"
DEFAULT_EVENTS_PATH = "data/15946.json"
PROVIDER_SCHEMA_VERSION = "statsbomb-open-data"
TRANSFORMATION_VERSION = "events_transform/v1"


def sha256_file(path: Path) -> str:
    # I hash the raw JSON so ingestion_runs.source_hash is reproducible.
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pipeline(
    events_path: str | Path,
    *,
    database_url: str = DEFAULT_DATABASE_URL,
    match_record: Mapping[str, Any] | None = None,
    data_dir: str | Path | None = None,
) -> None:
    path = Path(events_path)
    if not path.is_file():
        raise FileNotFoundError(f"StatsBomb events file not found: {path}")

    match_id = int(path.stem) if path.stem.isdigit() else None
    if match_id is None:
        raise ValueError("Expected a numeric match_id filename such as 15946.json")

    events = transform_statsbomb_events(path, match_id=match_id)
    source_hash = sha256_file(path)
    lineup_path = ensure_lineup_path(path, match_id)
    catalogue_dir = Path(data_dir) if data_dir is not None else path.parent

    with psycopg.connect(database_url) as conn:
        # I seed match_teams/match_players first so events COPY can satisfy FKs.
        seed_match_context(
            conn,
            match_id=match_id,
            events_path=path,
            lineup_path=lineup_path,
            source_hash=sha256_file(lineup_path),
            match_record=match_record,
            data_dir=catalogue_dir,
        )
        result = load_events(
            conn,
            events,
            source_uri=str(path.resolve()),
            source_hash=source_hash,
            provider_schema_version=PROVIDER_SCHEMA_VERSION,
            transformation_version=TRANSFORMATION_VERSION,
        )

    logger.info(
        "Finished run_id=%s status=%s inserted_count=%s",
        result.run_id,
        result.status,
        result.inserted_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load one StatsBomb events JSON into Postgres.")
    parser.add_argument(
        "events_path",
        nargs="?",
        default=DEFAULT_EVENTS_PATH,
        help="Path to a StatsBomb events JSON file (default: data/15946.json)",
    )
    parser.add_argument(
        "--database-url",
        default=DEFAULT_DATABASE_URL,
        help="Postgres URL (default: postgresql://postgres@localhost:5432/footballanalysis)",
    )
    args = parser.parse_args()
    run_pipeline(args.events_path, database_url=args.database_url)


if __name__ == "__main__":
    main()
