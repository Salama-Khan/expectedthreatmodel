"""I seed match dimension rows so events COPY can satisfy foreign keys."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

import psycopg

PROVIDER_SCHEMA_VERSION = "statsbomb-open-data"
TRANSFORMATION_VERSION = "match_context/v1"

logger = logging.getLogger(__name__)

STUB_COMPETITION_ID = 0
STUB_SEASON_ID = 0
OPEN_DATA_LINEUPS = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data/lineups/{match_id}.json"
)


@dataclass(frozen=True, slots=True)
class MatchMetadata:
    match_id: int
    competition_id: int
    competition_name: str
    country_name: str
    season_id: int
    season_name: str
    match_date: date
    kick_off: time | None
    home_team_id: int
    away_team_id: int
    home_score: int | None
    away_score: int | None


def ensure_lineup_path(events_path: Path, match_id: int) -> Path:
    """I return a local lineup JSON, downloading StatsBomb open data if needed."""
    candidates = [
        events_path.parent / "lineups" / f"{match_id}.json",
        events_path.parent / f"{match_id}_lineups.json",
        events_path.with_name(f"{match_id}_lineups.json"),
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path

    dest = candidates[-1]
    url = OPEN_DATA_LINEUPS.format(match_id=match_id)
    logger.info("Downloading lineups for match_id=%s from open-data", match_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    return dest


def parse_match_record(record: Mapping[str, Any]) -> MatchMetadata:
    """I turn one StatsBomb matches.json object into typed match context."""
    competition = record.get("competition") or {}
    season = record.get("season") or {}
    home = record.get("home_team") or {}
    away = record.get("away_team") or {}
    return MatchMetadata(
        match_id=int(record["match_id"]),
        competition_id=int(competition["competition_id"]),
        competition_name=str(competition.get("competition_name") or "Competition"),
        country_name=str(competition.get("country_name") or "Unknown"),
        season_id=int(season["season_id"]),
        season_name=str(season.get("season_name") or "Season"),
        match_date=_parse_date(record.get("match_date")),
        kick_off=_parse_kick_off(record.get("kick_off")),
        home_team_id=int(home.get("home_team_id") or home["team_id"]),
        away_team_id=int(away.get("away_team_id") or away["team_id"]),
        home_score=_optional_int(record.get("home_score")),
        away_score=_optional_int(record.get("away_score")),
    )


def find_match_metadata(
    match_id: int,
    *,
    data_dir: Path,
    match_record: Mapping[str, Any] | None = None,
) -> MatchMetadata | None:
    if match_record is not None:
        return parse_match_record(match_record)
    for record in iter_local_match_records(data_dir):
        if int(record.get("match_id") or 0) == match_id:
            return parse_match_record(record)
    return None


def iter_local_match_records(data_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("matches/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            records.extend(row for row in payload if isinstance(row, dict))
    return records


def seed_match_context(
    conn: psycopg.Connection[Any],
    *,
    match_id: int,
    events_path: Path,
    lineup_path: Path,
    source_hash: str,
    match_record: Mapping[str, Any] | None = None,
    data_dir: Path | None = None,
) -> UUID:
    """I upsert competitions, seasons, match, teams, players, and associations."""
    lineup = json.loads(lineup_path.read_text(encoding="utf-8"))
    events = json.loads(events_path.read_text(encoding="utf-8"))
    if not isinstance(lineup, list) or len(lineup) < 2:
        raise ValueError("I need a StatsBomb lineups array with two teams")

    teams, players, memberships = _collect_entities(lineup, events)
    lineup_team_ids = {int(block["team_id"]) for block in lineup[:2]}
    memberships = [row for row in memberships if row[0] in lineup_team_ids]
    metadata = find_match_metadata(
        match_id,
        data_dir=data_dir or events_path.parent,
        match_record=match_record,
    )

    with conn.transaction():
        run_row = conn.execute(
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
                str(lineup_path.resolve()),
                source_hash,
                PROVIDER_SCHEMA_VERSION,
                TRANSFORMATION_VERSION,
            ),
        ).fetchone()
        if run_row is None:
            raise RuntimeError("I expected a run_id back from ingestion_runs")
        run_id = run_row[0]
        if not isinstance(run_id, UUID):
            run_id = UUID(str(run_id))

        _upsert_match_frame(conn, match_id=match_id, run_id=run_id, metadata=metadata)

        for team_id, team_name in teams.items():
            conn.execute(
                """
                INSERT INTO teams (team_id, team_name)
                VALUES (%s, %s)
                ON CONFLICT (team_id) DO UPDATE
                SET team_name = EXCLUDED.team_name
                """,
                (team_id, team_name),
            )
        for player_id, (player_name, nickname) in players.items():
            conn.execute(
                """
                INSERT INTO players (player_id, player_name, nickname)
                VALUES (%s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE
                SET
                    player_name = EXCLUDED.player_name,
                    nickname = COALESCE(EXCLUDED.nickname, players.nickname)
                """,
                (player_id, player_name, nickname),
            )

        home_team_id, away_team_id = _home_away_ids(lineup, metadata)
        _upsert_match_team(conn, match_id, home_team_id, "home")
        _upsert_match_team(conn, match_id, away_team_id, "away")
        if metadata is not None:
            _apply_catalogue_scores(conn, metadata)

        for team_id, player_id, jersey_number, is_starter in memberships:
            conn.execute(
                """
                INSERT INTO match_players (
                    match_id, team_id, player_id, jersey_number, is_starter
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (match_id, team_id, player_id) DO NOTHING
                """,
                (match_id, team_id, player_id, jersey_number, is_starter),
            )

        conn.execute(
            """
            UPDATE ingestion_runs
            SET
                status = 'completed',
                inserted_count = %s,
                completed_at = NOW()
            WHERE run_id = %s
            """,
            (len(memberships), run_id),
        )

    logger.info(
        "Seeded match_id=%s teams=%s players=%s memberships=%s competition=%s",
        match_id,
        len(teams),
        len(players),
        len(memberships),
        metadata.competition_name if metadata else "Unknown",
    )
    return run_id


def refresh_match_metadata(
    conn: psycopg.Connection[Any],
    data_dir: Path,
) -> int:
    """I patch already-ingested matches with catalogue date, score, and home/away."""
    updated = 0
    with conn.transaction():
        for record in iter_local_match_records(data_dir):
            metadata = parse_match_record(record)
            exists = conn.execute(
                "SELECT 1 FROM matches WHERE match_id = %s",
                (metadata.match_id,),
            ).fetchone()
            if exists is None:
                continue
            _upsert_match_frame(
                conn,
                match_id=metadata.match_id,
                run_id=None,
                metadata=metadata,
            )
            _apply_catalogue_scores(conn, metadata)
            updated += 1
    logger.info("Refreshed metadata for %s ingested matches", updated)
    return updated


def _upsert_match_frame(
    conn: psycopg.Connection[Any],
    *,
    match_id: int,
    run_id: UUID | None,
    metadata: MatchMetadata | None,
) -> None:
    if metadata is None:
        conn.execute(
            """
            INSERT INTO competitions (competition_id, competition_name, country_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (competition_id) DO NOTHING
            """,
            (STUB_COMPETITION_ID, "Unknown", "Unknown"),
        )
        conn.execute(
            """
            INSERT INTO seasons (season_id, competition_id, season_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (season_id) DO NOTHING
            """,
            (STUB_SEASON_ID, STUB_COMPETITION_ID, "Unknown"),
        )
        if run_id is None:
            return
        conn.execute(
            """
            INSERT INTO matches (match_id, run_id, season_id, match_date)
            VALUES (%s, %s, %s, CURRENT_DATE)
            ON CONFLICT (match_id) DO NOTHING
            """,
            (match_id, run_id, STUB_SEASON_ID),
        )
        return

    conn.execute(
        """
        INSERT INTO competitions (competition_id, competition_name, country_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (competition_id) DO UPDATE
        SET
            competition_name = EXCLUDED.competition_name,
            country_name = EXCLUDED.country_name
        """,
        (metadata.competition_id, metadata.competition_name, metadata.country_name),
    )
    conn.execute(
        """
        INSERT INTO seasons (season_id, competition_id, season_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (season_id) DO UPDATE
        SET
            competition_id = EXCLUDED.competition_id,
            season_name = EXCLUDED.season_name
        """,
        (metadata.season_id, metadata.competition_id, metadata.season_name),
    )
    if run_id is None:
        conn.execute(
            """
            UPDATE matches
            SET
                season_id = %s,
                match_date = %s,
                kick_off = %s,
                home_score = %s,
                away_score = %s
            WHERE match_id = %s
            """,
            (
                metadata.season_id,
                metadata.match_date,
                metadata.kick_off,
                metadata.home_score,
                metadata.away_score,
                match_id,
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO matches (
            match_id, run_id, season_id, match_date, kick_off, home_score, away_score
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id) DO UPDATE
        SET
            season_id = EXCLUDED.season_id,
            match_date = EXCLUDED.match_date,
            kick_off = EXCLUDED.kick_off,
            home_score = EXCLUDED.home_score,
            away_score = EXCLUDED.away_score
        """,
        (
            match_id,
            run_id,
            metadata.season_id,
            metadata.match_date,
            metadata.kick_off,
            metadata.home_score,
            metadata.away_score,
        ),
    )


def _home_away_ids(
    lineup: list[dict[str, Any]],
    metadata: MatchMetadata | None,
) -> tuple[int, int]:
    if metadata is not None:
        return metadata.home_team_id, metadata.away_team_id
    return int(lineup[0]["team_id"]), int(lineup[1]["team_id"])


def _upsert_match_team(
    conn: psycopg.Connection[Any],
    match_id: int,
    team_id: int,
    side: str,
) -> None:
    conn.execute(
        """
        INSERT INTO match_teams (match_id, team_id, home_or_away)
        VALUES (%s, %s, %s)
        ON CONFLICT (match_id, team_id) DO NOTHING
        """,
        (match_id, team_id, side),
    )


def _apply_catalogue_scores(conn: psycopg.Connection[Any], metadata: MatchMetadata) -> None:
    """I attach catalogue scores to the home/away labels already stored.

    Lineup files are not ordered home/away, so I do not rewrite match_teams
    (that unique swap needs a table lock). I flip the scoreline if the stored
    home team is StatsBomb's away team.
    """
    row = conn.execute(
        """
        SELECT team_id
        FROM match_teams
        WHERE match_id = %s AND home_or_away = 'home'
        """,
        (metadata.match_id,),
    ).fetchone()
    home_score = metadata.home_score
    away_score = metadata.away_score
    if row is not None and int(row[0]) == metadata.away_team_id:
        home_score, away_score = metadata.away_score, metadata.home_score
    conn.execute(
        """
        UPDATE matches
        SET home_score = %s, away_score = %s
        WHERE match_id = %s
        """,
        (home_score, away_score, metadata.match_id),
    )


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_kick_off(value: Any) -> time | None:
    if not value:
        return None
    text = str(value).split(".")[0]
    return time.fromisoformat(text)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _collect_entities(
    lineup: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[
    dict[int, str],
    dict[int, tuple[str, str | None]],
    list[tuple[int, int, int | None, bool]],
]:
    teams: dict[int, str] = {}
    players: dict[int, tuple[str, str | None]] = {}
    memberships: dict[tuple[int, int], tuple[int | None, bool]] = {}

    for team_block in lineup:
        team_id = int(team_block["team_id"])
        teams[team_id] = str(team_block.get("team_name") or f"Team {team_id}")
        for slot in team_block.get("lineup") or []:
            player_id = int(slot["player_id"])
            name = str(slot.get("player_name") or f"Player {player_id}")
            nickname = slot.get("player_nickname")
            players[player_id] = (name, str(nickname) if nickname else None)
            jersey = slot.get("jersey_number")
            jersey_number = int(jersey) if jersey is not None else None
            memberships[(team_id, player_id)] = (jersey_number, _is_starter(slot))

    # I also walk events so a recipient missing from the lineup still gets a match_players row.
    for event in events:
        team = event.get("team") or {}
        if "id" in team:
            team_id = int(team["id"])
            teams.setdefault(team_id, str(team.get("name") or f"Team {team_id}"))
            player = event.get("player")
            if player and "id" in player:
                _remember_player(players, memberships, team_id, player)
            for slot in (event.get("tactics") or {}).get("lineup") or []:
                nested = slot.get("player") or {}
                if "id" in nested:
                    _remember_player(players, memberships, team_id, nested)
        recipient = (event.get("pass") or {}).get("recipient")
        event_team_id = int(team["id"]) if "id" in team else None
        if recipient and "id" in recipient and event_team_id is not None:
            _remember_player(players, memberships, event_team_id, recipient)

    rows = [
        (team_id, player_id, jersey, starter)
        for (team_id, player_id), (jersey, starter) in memberships.items()
    ]
    return teams, players, rows


def _remember_player(
    players: dict[int, tuple[str, str | None]],
    memberships: dict[tuple[int, int], tuple[int | None, bool]],
    team_id: int,
    player: dict[str, Any],
) -> None:
    player_id = int(player["id"])
    name = str(player.get("name") or f"Player {player_id}")
    players.setdefault(player_id, (name, None))
    memberships.setdefault((team_id, player_id), (None, False))


def _is_starter(slot: dict[str, Any]) -> bool:
    positions = slot.get("positions") or []
    return any(position.get("start_reason") == "Starting XI" for position in positions)
