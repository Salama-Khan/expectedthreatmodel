"""Serve the fitted xT surface and match-level football analytics."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from api_schemas import (
    MatchAction,
    MatchAnalyticsResponse,
    MatchSummary,
    MatchTeam,
    PlayerImpact,
    TeamThreatSummary,
    ThreatSurfaceResponse,
    ZoneThreat,
)
from match_analytics import player_impacts, score_match_actions, team_threat_summaries
from xt_engine import (
    DEFAULT_GRID_COLUMNS,
    DEFAULT_GRID_ROWS,
    MOVES_QUERY,
    SHOTS_QUERY,
    TRANSITIONS_QUERY,
    fit_xt_surface,
)

MODEL_ID = "singh-xt"
MODEL_VERSION = "v1"
DEFAULT_DATABASE_URL = "postgresql://postgres@localhost:5432/footballanalysis"

DbConnection = psycopg.Connection[dict[str, Any]]

# Fallback labels when catalogue metadata was not seeded for a showcase match.
SHOWCASE_METADATA: dict[int, dict[str, Any]] = {
    15946: {
        "competition": "La Liga",
        "season": "2018/19",
        "match_date": "2018-08-18",
        "home_score": 3,
        "away_score": 0,
    },
    8658: {
        "competition": "FIFA World Cup",
        "season": "2018",
        "match_date": "2018-07-15",
        "home_score": 4,
        "away_score": 2,
    },
}


@dataclass(frozen=True)
class SurfaceSnapshot:
    values: tuple[float, ...]
    training_matches: int
    training_actions: int


_surface_snapshot: SurfaceSnapshot | None = None
_surface_lock = Lock()

app = FastAPI(
    title="Expected Threat Explorer API",
    description="Match actions and a Karun Singh-style Expected Threat surface.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_connection() -> Iterator[DbConnection]:
    """Yield a read-only psycopg connection for one request."""
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        yield conn


def table_from_rows(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> dict[str, list[Any]]:
    """Pivot dictionary rows into the column-oriented table used by the solver."""
    return {column: [row[column] for row in rows] for column in columns}


def fitted_surface(conn: DbConnection) -> SurfaceSnapshot:
    """Fit the global model and reuse it until more matches are ingested."""
    global _surface_snapshot
    stats = conn.execute(
        """
        SELECT
            COUNT(DISTINCT match_id) AS training_matches,
            COUNT(*) FILTER (
                WHERE type_name IN ('Pass', 'Carry', 'Shot')
            ) AS training_actions
        FROM events
        """
    ).fetchone()
    training_matches = int(stats["training_matches"]) if stats else 0
    training_actions = int(stats["training_actions"]) if stats else 0

    cached = _surface_snapshot
    if (
        cached is not None
        and cached.training_matches == training_matches
        and cached.training_actions == training_actions
    ):
        return cached

    with _surface_lock:
        cached = _surface_snapshot
        if (
            cached is not None
            and cached.training_matches == training_matches
            and cached.training_actions == training_actions
        ):
            return cached

        shots = table_from_rows(
            conn.execute(SHOTS_QUERY).fetchall(),
            ("zone_index", "shot_attempts", "goals"),
        )
        moves = table_from_rows(
            conn.execute(MOVES_QUERY).fetchall(),
            ("zone_index", "move_attempts", "successful_moves"),
        )
        transitions = table_from_rows(
            conn.execute(TRANSITIONS_QUERY).fetchall(),
            ("origin_zone", "dest_zone", "n"),
        )
        xt_values = fit_xt_surface(
            shots,
            moves,
            transitions,
            grid_columns=DEFAULT_GRID_COLUMNS,
            grid_rows=DEFAULT_GRID_ROWS,
        )
        _surface_snapshot = SurfaceSnapshot(
            values=tuple(float(value) for value in xt_values),
            training_matches=training_matches,
            training_actions=training_actions,
        )
        return _surface_snapshot


@app.get("/api/health")
def health(conn: DbConnection = Depends(get_connection)) -> dict[str, str]:
    conn.execute("SELECT 1")
    return {"status": "ok"}


def _apply_showcase_metadata(
    match_id: int,
    *,
    competition: str,
    season: str,
    match_date: Any,
    home_score: Any,
    away_score: Any,
) -> dict[str, Any]:
    fallback = SHOWCASE_METADATA.get(match_id, {})
    return {
        "competition": (
            str(fallback.get("competition"))
            if competition == "Unknown" and fallback.get("competition")
            else competition
        ),
        "season": (
            str(fallback.get("season"))
            if season == "Unknown" and fallback.get("season")
            else season
        ),
        "match_date": str(fallback.get("match_date") or match_date),
        "home_score": (
            int(home_score)
            if home_score is not None
            else fallback.get("home_score")
        ),
        "away_score": (
            int(away_score)
            if away_score is not None
            else fallback.get("away_score")
        ),
    }


@app.get("/api/matches", response_model=list[MatchSummary])
def list_matches(conn: DbConnection = Depends(get_connection)) -> list[MatchSummary]:
    """Return ingested matches so the UI can switch fixtures."""
    rows = conn.execute(
        """
        SELECT
            m.match_id,
            m.match_date,
            m.home_score,
            m.away_score,
            COALESCE(c.competition_name, 'Competition') AS competition,
            COALESCE(s.season_name, 'Season') AS season,
            home.team_name AS home_team,
            away.team_name AS away_team
        FROM matches m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN competitions c ON c.competition_id = s.competition_id
        JOIN match_teams mth
            ON mth.match_id = m.match_id AND mth.home_or_away = 'home'
        JOIN teams home ON home.team_id = mth.team_id
        JOIN match_teams mta
            ON mta.match_id = m.match_id AND mta.home_or_away = 'away'
        JOIN teams away ON away.team_id = mta.team_id
        WHERE EXISTS (SELECT 1 FROM events e WHERE e.match_id = m.match_id)
        ORDER BY m.match_date, m.match_id
        """
    ).fetchall()
    matches: list[MatchSummary] = []
    for row in rows:
        labels = _apply_showcase_metadata(
            int(row["match_id"]),
            competition=str(row["competition"]),
            season=str(row["season"]),
            match_date=row["match_date"],
            home_score=row["home_score"],
            away_score=row["away_score"],
        )
        matches.append(
            MatchSummary(
                match_id=int(row["match_id"]),
                competition=str(labels["competition"]),
                season=str(labels["season"]),
                match_date=str(labels["match_date"]),
                home_team=str(row["home_team"]),
                away_team=str(row["away_team"]),
                home_score=labels["home_score"],
                away_score=labels["away_score"],
            )
        )
    return matches


@app.get("/api/xt-surface", response_model=ThreatSurfaceResponse)
def xt_surface(
    conn: DbConnection = Depends(get_connection),
) -> ThreatSurfaceResponse:
    snapshot = fitted_surface(conn)
    return ThreatSurfaceResponse(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        grid_columns=DEFAULT_GRID_COLUMNS,
        grid_rows=DEFAULT_GRID_ROWS,
        training_matches=snapshot.training_matches,
        training_actions=snapshot.training_actions,
        zones=[
            ZoneThreat(zone_index=index, xt_value=float(value))
            for index, value in enumerate(snapshot.values)
        ],
    )


@app.get(
    "/api/matches/{match_id}/analytics",
    response_model=MatchAnalyticsResponse,
)
def match_analytics(
    match_id: int,
    conn: DbConnection = Depends(get_connection),
) -> MatchAnalyticsResponse:
    """Return match context, scored actions, and player/team summaries."""
    context_rows = conn.execute(
        """
        SELECT
            m.match_id,
            m.match_date,
            m.home_score,
            m.away_score,
            COALESCE(c.competition_name, 'Competition') AS competition,
            COALESCE(s.season_name, 'Season') AS season,
            mt.home_or_away AS side,
            t.team_id,
            t.team_name
        FROM matches m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN competitions c ON c.competition_id = s.competition_id
        JOIN match_teams mt ON mt.match_id = m.match_id
        JOIN teams t ON t.team_id = mt.team_id
        WHERE m.match_id = %s
        ORDER BY CASE mt.home_or_away WHEN 'home' THEN 0 ELSE 1 END
        """,
        (match_id,),
    ).fetchall()
    if len(context_rows) != 2:
        raise HTTPException(status_code=404, detail="Match context is unavailable")

    action_rows = conn.execute(
        """
        SELECT
            e.event_id,
            e.event_index,
            e.minute,
            e.second,
            e.team_id,
            t.team_name,
            e.actor_player_id AS player_id,
            COALESCE(NULLIF(p.nickname, ''), p.player_name) AS player_name,
            e.type_name AS action_type,
            e.outcome,
            e.location_x,
            e.location_y,
            e.end_location_x,
            e.end_location_y
        FROM events e
        JOIN teams t ON t.team_id = e.team_id
        LEFT JOIN players p ON p.player_id = e.actor_player_id
        WHERE e.match_id = %s
          AND e.type_name IN ('Pass', 'Carry', 'Shot')
          AND e.location_x IS NOT NULL
          AND e.location_y IS NOT NULL
        ORDER BY e.event_index
        """,
        (match_id,),
    ).fetchall()

    snapshot = fitted_surface(conn)
    actions = score_match_actions(
        action_rows,
        snapshot.values,
        grid_columns=DEFAULT_GRID_COLUMNS,
        grid_rows=DEFAULT_GRID_ROWS,
    )
    first = context_rows[0]
    labels = _apply_showcase_metadata(
        match_id,
        competition=str(first["competition"]),
        season=str(first["season"]),
        match_date=first["match_date"],
        home_score=first["home_score"],
        away_score=first["away_score"],
    )
    teams = [
        MatchTeam(
            team_id=int(row["team_id"]),
            team_name=str(row["team_name"]),
            side=str(row["side"]),
            score=(
                labels["home_score"]
                if row["side"] == "home"
                else labels["away_score"]
            ),
        )
        for row in context_rows
    ]

    return MatchAnalyticsResponse(
        match_id=match_id,
        competition=str(labels["competition"]),
        season=str(labels["season"]),
        match_date=str(labels["match_date"]),
        teams=teams,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        actions=[MatchAction(**action) for action in actions],
        players=[PlayerImpact(**player) for player in player_impacts(actions)],
        team_summaries=[
            TeamThreatSummary(**team) for team in team_threat_summaries(actions)
        ],
    )
