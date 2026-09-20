"""Ingest a StatsBomb Open Data competition-season into Postgres."""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from pathlib import Path

import psycopg

from run_pipeline import DEFAULT_DATABASE_URL, run_pipeline
from seed_match_context import refresh_match_metadata

logger = logging.getLogger(__name__)

OPEN_DATA_MATCHES = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches"
    "/{competition_id}/{season_id}.json"
)
OPEN_DATA_EVENTS = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/{match_id}.json"
)


def download_json(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s", url)
    urllib.request.urlretrieve(url, dest)
    return dest


def ingest_competition_season(
    competition_id: int,
    season_id: int,
    *,
    data_dir: Path,
    database_url: str,
    limit: int | None = None,
) -> None:
    matches_path = download_json(
        OPEN_DATA_MATCHES.format(competition_id=competition_id, season_id=season_id),
        data_dir / "matches" / f"{competition_id}_{season_id}.json",
    )
    matches = json.loads(matches_path.read_text(encoding="utf-8"))
    selected = list(matches)
    if limit is not None:
        selected = selected[:limit]
    logger.info("Ingesting %s matches from competition %s season %s", len(selected), competition_id, season_id)
    for record in selected:
        match_id = int(record["match_id"])
        events_path = download_json(
            OPEN_DATA_EVENTS.format(match_id=match_id),
            data_dir / f"{match_id}.json",
        )
        try:
            run_pipeline(
                events_path,
                database_url=database_url,
                match_record=record,
                data_dir=data_dir,
            )
        except Exception:
            logger.exception("Skipped match_id=%s after a load error", match_id)
    with psycopg.connect(database_url) as conn:
        refresh_match_metadata(conn, data_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Ingest every match in one StatsBomb Open Data competition-season."
    )
    parser.add_argument("--competition", type=int, help="StatsBomb competition_id")
    parser.add_argument("--season", type=int, help="StatsBomb season_id")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap for a trial run")
    parser.add_argument("--data-dir", default="data", help="Where to store downloaded JSON")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument(
        "--refresh-metadata-only",
        action="store_true",
        help="Patch already-ingested matches from local matches/*.json catalogues",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    if args.refresh_metadata_only:
        with psycopg.connect(args.database_url) as conn:
            refresh_match_metadata(conn, data_dir)
        return
    if args.competition is None or args.season is None:
        parser.error("--competition and --season are required unless --refresh-metadata-only is set")
    ingest_competition_season(
        args.competition,
        args.season,
        data_dir=data_dir,
        database_url=args.database_url,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
