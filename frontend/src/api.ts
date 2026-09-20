const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export type ZoneThreat = {
  zone_index: number;
  xt_value: number;
};

export type ThreatSurface = {
  model_id: string;
  model_version: string;
  grid_columns: number;
  grid_rows: number;
  training_matches: number;
  training_actions: number;
  zones: ZoneThreat[];
};

export type MatchTeam = {
  team_id: number;
  team_name: string;
  side: "home" | "away";
  score: number | null;
};

export type MatchAction = {
  event_id: string;
  event_index: number;
  minute: number;
  second: number;
  team_id: number;
  team_name: string;
  player_id: number | null;
  player_name: string | null;
  action_type: "Pass" | "Carry" | "Shot";
  outcome: string | null;
  location_x: number;
  location_y: number;
  end_location_x: number | null;
  end_location_y: number | null;
  xt_start: number;
  xt_end: number | null;
  delta_xt: number | null;
};

export type PlayerImpact = {
  player_id: number;
  player_name: string;
  team_id: number;
  team_name: string;
  actions: number;
  positive_xt: number;
  net_xt: number;
  best_action_xt: number;
};

export type TeamThreatSummary = {
  team_id: number;
  team_name: string;
  positive_xt: number;
  net_xt: number;
  actions: number;
  shots: number;
};

export type MatchAnalytics = {
  match_id: number;
  competition: string;
  season: string;
  match_date: string;
  teams: MatchTeam[];
  model_id: string;
  model_version: string;
  actions: MatchAction[];
  players: PlayerImpact[];
  team_summaries: TeamThreatSummary[];
};

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchSurface(signal?: AbortSignal): Promise<ThreatSurface> {
  return getJson<ThreatSurface>("/api/xt-surface", signal);
}

export function fetchMatchAnalytics(
  matchId: number,
  signal?: AbortSignal,
): Promise<MatchAnalytics> {
  return getJson<MatchAnalytics>(`/api/matches/${matchId}/analytics`, signal);
}

export type MatchSummary = {
  match_id: number;
  competition: string;
  season: string;
  match_date: string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
};

export function fetchMatches(signal?: AbortSignal): Promise<MatchSummary[]> {
  return getJson<MatchSummary[]>("/api/matches", signal);
}
