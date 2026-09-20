from __future__ import annotations

from pydantic import BaseModel, Field

class ZoneThreat(BaseModel):
    zone_index: int
    xt_value: float = Field(ge=0.0, le=1.0)

class ThreatSurfaceResponse(BaseModel):
    model_id: str
    model_version: str
    grid_columns: int
    grid_rows: int
    training_matches: int
    training_actions: int
    zones: list[ZoneThreat]


class MatchTeam(BaseModel):
    team_id: int
    team_name: str
    side: str
    score: int | None = None


class MatchAction(BaseModel):
    event_id: str
    event_index: int
    minute: int
    second: float
    team_id: int
    team_name: str
    player_id: int | None = None
    player_name: str | None = None
    action_type: str
    outcome: str | None = None
    location_x: float
    location_y: float
    end_location_x: float | None = None
    end_location_y: float | None = None
    xt_start: float
    xt_end: float | None = None
    delta_xt: float | None = None


class PlayerImpact(BaseModel):
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    actions: int
    positive_xt: float
    net_xt: float
    best_action_xt: float


class TeamThreatSummary(BaseModel):
    team_id: int
    team_name: str
    positive_xt: float
    net_xt: float
    actions: int
    shots: int


class MatchAnalyticsResponse(BaseModel):
    match_id: int
    competition: str
    season: str
    match_date: str
    teams: list[MatchTeam]
    model_id: str
    model_version: str
    actions: list[MatchAction]
    players: list[PlayerImpact]
    team_summaries: list[TeamThreatSummary]


class MatchSummary(BaseModel):
    match_id: int
    competition: str
    season: str
    match_date: str
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None