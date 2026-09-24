"""Serve the fitted xT surface and match-level football analytics."""

from __future__ import annotations

import os
from collections.abc import Iterator
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
from match_analytics import (
    actions_from_stored_threat,
    player_impacts,
    team_threat_summaries,
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


def stored_surface(conn: DbConnection) -> tuple[list[float], int, int]:
    """Read the fitted vector written at load time."""
    row = conn.execute(
        """
        SELECT grid_columns, grid_rows, validation_metrics
        FROM xt_models
        WHERE model_id = %s AND model_version = %s AND status = 'ready'
        """,
        (MODEL_ID, MODEL_VERSION),
    ).fetchone()
    metrics = row["validation_metrics"] if row else None
    values = metrics.get("xt") if isinstance(metrics, dict) else None
    if row is None or not values:
        raise HTTPException(status_code=404, detail="xT model singh-xt/v1 is not stored")
    return (
        [float(value) for value in values],
        int(row["grid_columns"]),
        int(row["grid_rows"]),
    )


def training_counts(conn: DbConnection) -> tuple[int, int]:
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
    if stats is None:
        return 0, 0
    return int(stats["training_matches"]), int(stats["training_actions"])


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
    values, grid_columns, grid_rows = stored_surface(conn)
    training_matches, training_actions = training_counts(conn)
    return ThreatSurfaceResponse(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
        training_matches=training_matches,
        training_actions=training_actions,
        zones=[
            ZoneThreat(zone_index=index, xt_value=float(value))
            for index, value in enumerate(values)
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
            e.end_location_y,
            et.xt_start,
            et.xt_end,
            et.delta_xt
        FROM events e
        JOIN teams t ON t.team_id = e.team_id
        LEFT JOIN players p ON p.player_id = e.actor_player_id
        LEFT JOIN event_threat et
            ON et.event_id = e.event_id
           AND et.model_id = %s
           AND et.model_version = %s
        WHERE e.match_id = %s
          AND e.type_name IN ('Pass', 'Carry', 'Shot')
          AND e.location_x IS NOT NULL
          AND e.location_y IS NOT NULL
        ORDER BY e.event_index
        """,
        (MODEL_ID, MODEL_VERSION, match_id),
    ).fetchall()

    values, grid_columns, grid_rows = stored_surface(conn)
    actions = actions_from_stored_threat(
        action_rows,
        values,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
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
