import { useEffect, useMemo, useState } from "react";

import {
  fetchMatchAnalytics,
  fetchMatches,
  fetchSurface,
  type MatchAction,
  type MatchAnalytics,
  type MatchSummary,
  type ThreatSurface,
} from "./api";
import InteractivePitch from "./InteractivePitch";

const SHOWCASE_MATCH_ID = 15946;
const ACTION_FILTERS = ["All", "Pass", "Carry", "Shot"] as const;
const PRIMER_STORAGE_KEY = "touchline-primer-collapsed";

type View = "match" | "players" | "model";
type ActionFilter = (typeof ACTION_FILTERS)[number];

function readPrimerOpen(): boolean {
  try {
    return window.localStorage.getItem(PRIMER_STORAGE_KEY) !== "1";
  } catch {
    return true;
  }
}

function persistPrimerOpen(open: boolean): void {
  try {
    window.localStorage.setItem(PRIMER_STORAGE_KEY, open ? "0" : "1");
  } catch {
    /* Private mode can block localStorage. */
  }
}

function actionValueCopy(action: MatchAction): string {
  if (action.action_type === "Shot") {
    return "Shots are mapped by location only. They end the possession, so there is no movement delta.";
  }
  if (action.action_type === "Pass" && action.outcome != null) {
    return "Incomplete pass: the attack loses the threat already present at the start zone (− start threat).";
  }
  return "Completed pass or carry: end-zone threat minus start-zone threat.";
}

function formatXt(value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined) return "—";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(3)}`;
}

function displayName(name: string | null | undefined): string {
  if (!name) return "—";
  const parts = name.trim().split(/\s+/);
  if (parts.length <= 2) return name;
  return parts[0];
}

function formatDate(value: string): string {
  const date = new Date(`${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
}

function StatCard({
  label,
  value,
  note,
  definition,
}: {
  label: string;
  value: string;
  note: string;
  definition: string;
}) {
  const [explained, setExplained] = useState(false);
  return (
    <article className={explained ? "stat-card is-explained" : "stat-card"}>
      <span>
        {label}
        <button
          type="button"
          className="stat-info"
          aria-expanded={explained}
          aria-label={`What ${label} means`}
          title={definition}
          onClick={() => setExplained((open) => !open)}
        >
          ?
        </button>
      </span>
      <strong>{value}</strong>
      <small className={explained ? "stat-definition" : undefined}>
        {explained ? definition : note}
      </small>
    </article>
  );
}

function MomentumChart({
  analytics,
  onSelectAction,
}: {
  analytics: MatchAnalytics;
  onSelectAction: (action: MatchAction) => void;
}) {
  const width = 900;
  const height = 190;
  const padding = 28;
  const maxMinute = Math.max(95, ...analytics.actions.map((action) => action.minute));
  const teams = analytics.teams;
  const chartData = teams.map((team) => {
    let cumulative = 0;
    const points = Array.from({ length: 20 }, (_, index) => {
      const minute = (index / 19) * maxMinute;
      cumulative = analytics.actions
        .filter(
          (action) =>
            action.team_id === team.team_id &&
            action.minute <= minute &&
            (action.delta_xt ?? 0) > 0,
        )
        .reduce((sum, action) => sum + (action.delta_xt ?? 0), 0);
      return { minute, value: cumulative };
    });
    return { team, points };
  });
  const maximum = Math.max(
    0.01,
    ...chartData.flatMap(({ points }) => points.map((point) => point.value)),
  );
  const colours = ["#43d7c4", "#ff9f5a"];
  const x = (minute: number) =>
    padding + (minute / maxMinute) * (width - padding * 2);
  const y = (value: number) =>
    height - padding - (value / maximum) * (height - padding * 2);

  return (
    <section className="panel timeline-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Match flow</span>
          <h2>xT generated over time</h2>
        </div>
        <p>Cumulative positive value from completed passes and carries.</p>
      </div>
      <div className="timeline-chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cumulative xT timeline">
          {[0, 0.5, 1].map((ratio) => (
            <line
              key={ratio}
              x1={padding}
              y1={y(maximum * ratio)}
              x2={width - padding}
              y2={y(maximum * ratio)}
              className="chart-gridline"
            />
          ))}
          {[0, 15, 30, 45, 60, 75, 90].map((minute) => (
            <g key={minute}>
              <line
                x1={x(minute)}
                y1={padding}
                x2={x(minute)}
                y2={height - padding}
                className="chart-tick"
              />
              <text x={x(minute)} y={height - 7} textAnchor="middle">
                {minute}&apos;
              </text>
            </g>
          ))}
          {chartData.map(({ team, points }, index) => (
            <polyline
              key={team.team_id}
              points={points.map((point) => `${x(point.minute)},${y(point.value)}`).join(" ")}
              fill="none"
              stroke={colours[index]}
              strokeWidth={3}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {analytics.actions
            .filter((action) => (action.delta_xt ?? 0) >= 0.025)
            .map((action) => (
              <circle
                key={action.event_id}
                cx={x(action.minute)}
                cy={y(
                  analytics.actions
                    .filter(
                      (candidate) =>
                        candidate.team_id === action.team_id &&
                        candidate.minute <= action.minute &&
                        (candidate.delta_xt ?? 0) > 0,
                    )
                    .reduce((sum, candidate) => sum + (candidate.delta_xt ?? 0), 0),
                )}
                r={5}
                fill={colours[teams.findIndex((team) => team.team_id === action.team_id)]}
                className="chart-moment"
                role="button"
                tabIndex={0}
                onClick={() => onSelectAction(action)}
              />
            ))}
        </svg>
      </div>
      <div className="chart-legend">
        {teams.map((team, index) => (
          <span key={team.team_id}>
            <i style={{ background: colours[index] }} />
            {team.team_name}
          </span>
        ))}
      </div>
    </section>
  );
}

function PlayerView({ analytics }: { analytics: MatchAnalytics }) {
  const [teamId, setTeamId] = useState<number | "all">("all");
  const players = analytics.players.filter(
    (player) => teamId === "all" || player.team_id === teamId,
  );
  const maximum = Math.max(...players.map((player) => player.positive_xt), 0.001);

  return (
    <section className="content-section">
      <div className="section-intro">
        <div>
          <span className="eyebrow">Player impact</span>
          <h1>Who moved the match forward?</h1>
        </div>
        <p>
          Positive xT rewards completed passes and carries that move possession
          into more dangerous zones. Net xT subtracts value lost on incomplete
          passes, so a high-volume passer can rank below a safer one.
        </p>
      </div>
      <div className="filter-row" aria-label="Filter player rankings by team">
        <button
          className={teamId === "all" ? "filter-chip is-active" : "filter-chip"}
          onClick={() => setTeamId("all")}
        >
          Both teams
        </button>
        {analytics.teams.map((team) => (
          <button
            key={team.team_id}
            className={teamId === team.team_id ? "filter-chip is-active" : "filter-chip"}
            onClick={() => setTeamId(team.team_id)}
          >
            {team.team_name}
          </button>
        ))}
      </div>
      <div className="player-table panel">
        <div className="player-row player-row--head">
          <span>Player</span>
          <span>Actions</span>
          <span>Best</span>
          <span>Net xT</span>
          <span>Created</span>
        </div>
        {players.slice(0, 15).map((player, index) => (
          <div className="player-row" key={player.player_id}>
            <div className="player-identity">
              <b>{String(index + 1).padStart(2, "0")}</b>
              <span>
                <strong>{player.player_name}</strong>
                <small>{player.team_name}</small>
              </span>
            </div>
            <span>{player.actions}</span>
            <span>+{formatXt(player.best_action_xt)}</span>
            <span className={player.net_xt >= 0 ? "positive-value" : "negative-value"}>
              {formatXt(player.net_xt, true)}
            </span>
            <div className="impact-meter">
              <i style={{ width: `${(player.positive_xt / maximum) * 100}%` }} />
              <strong>{formatXt(player.positive_xt)}</strong>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ModelView({ surface }: { surface: ThreatSurface }) {
  const maxZone = surface.zones.reduce(
    (best, zone) => (zone.xt_value > best.xt_value ? zone : best),
    surface.zones[0],
  );
  return (
    <section className="content-section">
      <div className="section-intro">
        <div>
          <span className="eyebrow">The model</span>
          <h1>Where possession becomes dangerous</h1>
        </div>
        <p>
          Each zone estimates the probability that possession will eventually
          lead to a goal. This surface is shared across the match dropdown:
          switching games rescores that fixture’s actions; it does not refit
          the grid until more matches are ingested. Hover any cell to inspect
          its value.
        </p>
      </div>
      <div className="model-layout">
        <article className="panel pitch-panel model-pitch">
          <InteractivePitch surface={surface} mode="surface" />
        </article>
        <aside className="model-notes">
          <article className="panel explainer-card">
            <span className="eyebrow">Reading the surface</span>
            <h2>Threat rises near goal</h2>
            <p>
              The model learns whether players shoot or move from each zone, how
              often shots score, and where successful moves finish.
            </p>
            <div className="model-stat">
              <span>Peak zone</span>
              <strong>{maxZone.zone_index}</strong>
            </div>
            <div className="model-stat">
              <span>Peak xT</span>
              <strong>{maxZone.xt_value.toFixed(3)}</strong>
            </div>
          </article>
          <article className="panel explainer-card">
            <span className="eyebrow">Model scope</span>
            <h2>{surface.model_id} · {surface.model_version}</h2>
            <p>
              Trained from {surface.training_actions.toLocaleString()} on-ball
              actions across {surface.training_matches} match
              {surface.training_matches === 1 ? "" : "es"} on a{" "}
              {surface.grid_columns}×{surface.grid_rows} grid.
            </p>
            <small>
              Current prototype uses an unsmoothed Singh-style xT model. Values
              should be treated as exploratory until trained on a larger corpus.
              The coloured pitch is the global model, not a single-match heatmap.
            </small>
          </article>
        </aside>
      </div>
    </section>
  );
}

function MatchView({
  analytics,
  surface,
  primerOpen,
  onPrimerOpenChange,
}: {
  analytics: MatchAnalytics;
  surface: ThreatSurface;
  primerOpen: boolean;
  onPrimerOpenChange: (open: boolean) => void;
}) {
  const [teamFilter, setTeamFilter] = useState<number | "all">("all");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("All");
  const [positiveOnly, setPositiveOnly] = useState(true);
  const [showSurface, setShowSurface] = useState(false);
  const [selectedAction, setSelectedAction] = useState<MatchAction | null>(null);

  const filteredActions = useMemo(
    () =>
      analytics.actions.filter(
        (action) =>
          (teamFilter === "all" || action.team_id === teamFilter) &&
          (actionFilter === "All" || action.action_type === actionFilter) &&
          (!positiveOnly || action.action_type === "Shot" || (action.delta_xt ?? 0) > 0),
      ),
    [actionFilter, analytics.actions, positiveOnly, teamFilter],
  );
  const positiveActions = analytics.actions.filter((action) => (action.delta_xt ?? 0) > 0);
  const totalCreated = positiveActions.reduce(
    (sum, action) => sum + (action.delta_xt ?? 0),
    0,
  );
  const topPlayer = analytics.players[0];
  const topAction = positiveActions.reduce<MatchAction | null>(
    (best, action) =>
      best === null || (action.delta_xt ?? 0) > (best.delta_xt ?? 0) ? action : best,
    null,
  );
  const shotCount = analytics.actions.filter((action) => action.action_type === "Shot").length;

  return (
    <>
      <section className="match-hero">
        <div className="match-hero__meta">
          <span>{analytics.competition}</span>
          <i />
          <span>{analytics.season}</span>
          <i />
          <time>{formatDate(analytics.match_date)}</time>
        </div>
        <div className="scoreboard">
          <div>
            <span className="team-mark team-mark--home">
              {analytics.teams[0].team_name.slice(0, 1)}
            </span>
            <h1>{analytics.teams[0].team_name}</h1>
          </div>
          <strong>
            {analytics.teams[0].score ?? "–"}
            <small>FT</small>
            {analytics.teams[1].score ?? "–"}
          </strong>
          <div>
            <span className="team-mark team-mark--away">
              {analytics.teams[1].team_name.slice(0, 1)}
            </span>
            <h1>{analytics.teams[1].team_name}</h1>
          </div>
        </div>
      </section>

      <section className={primerOpen ? "guide-primer is-open" : "guide-primer"} aria-label="How this explorer works">
        <div className="guide-primer__bar">
          <span className="eyebrow">How this works</span>
          <button
            type="button"
            className="guide-primer__toggle"
            aria-expanded={primerOpen}
            onClick={() => onPrimerOpenChange(!primerOpen)}
          >
            {primerOpen ? "Hide" : "Show"}
          </button>
        </div>
        {primerOpen ? (
          <div className="guide-primer__body">
            <article>
              <h3>What you are seeing</h3>
              <p>
                This match’s passes, carries, and shots, valued with a 16×12
                Expected Threat surface. Moving into a more dangerous zone
                creates xT; turning the ball over loses it.
              </p>
            </article>
            <article>
              <h3>How to use it</h3>
              <p>
                Filter by team or action type, click a dot on the pitch, then
                read start vs end threat on the right.
              </p>
            </article>
            <article>
              <h3>What does not change</h3>
              <p>
                Model overlay colours come from every ingested match, not this
                fixture alone. Switching games rescores the actions; it does not
                rebuild the heatmap.
              </p>
            </article>
          </div>
        ) : null}
      </section>

      <section className="stats-grid" aria-label="Match xT summary">
        <StatCard
          label="Positive xT created"
          value={formatXt(totalCreated)}
          note={`${positiveActions.length} progressive actions`}
          definition="Sum of completed passes and carries that moved the ball into a higher-xT zone."
        />
        <StatCard
          label="Top creator"
          value={displayName(topPlayer?.player_name)}
          note={topPlayer ? `${formatXt(topPlayer.positive_xt)} xT created` : "No actions"}
          definition="The player with the most positive xT from completed progressive actions."
        />
        <StatCard
          label="Highest-value action"
          value={formatXt(topAction?.delta_xt)}
          note={topAction ? `${topAction.player_name} · ${topAction.minute}'` : "No actions"}
          definition="The single completed pass or carry with the largest xT gain in this match."
        />
        <StatCard
          label="Shots mapped"
          value={String(shotCount)}
          note="Shown on the pitch, no movement delta"
          definition="Shots appear on the map by location but do not receive a movement delta — they end the possession."
        />
      </section>

      <section className="match-workspace">
        <article className="panel pitch-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Action map</span>
              <h2>How possession gained value</h2>
            </div>
            <button
              className={showSurface ? "switch-button is-active" : "switch-button"}
              onClick={() => setShowSurface((value) => !value)}
              aria-pressed={showSurface}
            >
              <i />
              Model overlay
            </button>
          </div>
          <div className="pitch-filters">
            <div className="filter-row">
              <button
                className={teamFilter === "all" ? "filter-chip is-active" : "filter-chip"}
                onClick={() => setTeamFilter("all")}
              >
                Both teams
              </button>
              {analytics.teams.map((team) => (
                <button
                  key={team.team_id}
                  className={
                    teamFilter === team.team_id ? "filter-chip is-active" : "filter-chip"
                  }
                  onClick={() => setTeamFilter(team.team_id)}
                >
                  {team.team_name}
                </button>
              ))}
            </div>
            <div className="filter-row">
              {ACTION_FILTERS.map((filter) => (
                <button
                  key={filter}
                  className={actionFilter === filter ? "filter-chip is-active" : "filter-chip"}
                  onClick={() => setActionFilter(filter)}
                >
                  {filter}
                </button>
              ))}
              <button
                className={positiveOnly ? "filter-chip is-active" : "filter-chip"}
                onClick={() => setPositiveOnly((value) => !value)}
                aria-pressed={positiveOnly}
              >
                Positive xT only
              </button>
            </div>
          </div>
          <InteractivePitch
            surface={surface}
            actions={filteredActions}
            teams={analytics.teams}
            mode="actions"
            showHeatmap={showSurface}
            selectedActionId={selectedAction?.event_id}
            onSelectAction={setSelectedAction}
          />
        </article>

        <aside className="panel action-inspector" aria-live="polite">
          {selectedAction ? (
            <>
              <div>
                <span className="eyebrow">Selected action · {selectedAction.minute}&apos;</span>
                <h2>{selectedAction.player_name ?? selectedAction.team_name}</h2>
                <p>{selectedAction.team_name}</p>
              </div>
              <div className="action-value">
                <span>{selectedAction.action_type}</span>
                <strong>{formatXt(selectedAction.delta_xt, true)}</strong>
                <small>xT change</small>
              </div>
              <p className="action-value-copy">{actionValueCopy(selectedAction)}</p>
              <dl className="action-detail-list">
                <div>
                  <dt>Start threat</dt>
                  <dd>{formatXt(selectedAction.xt_start)}</dd>
                </div>
                <div>
                  <dt>End threat</dt>
                  <dd>{formatXt(selectedAction.xt_end)}</dd>
                </div>
                <div>
                  <dt>Outcome</dt>
                  <dd>{selectedAction.outcome ?? "Complete"}</dd>
                </div>
              </dl>
              <button className="text-button" onClick={() => setSelectedAction(null)}>
                Clear selection
              </button>
            </>
          ) : (
            <>
              <span className="eyebrow">How to explore</span>
              <h2>Select an action</h2>
              <p>
                Hover an action for a quick read, then select it to compare its
                starting and ending threat.
              </p>
              <ol className="explore-steps">
                <li><b>1</b><span>Filter by team or action type.</span></li>
                <li><b>2</b><span>Follow each action from left to right.</span></li>
                <li><b>3</b><span>Toggle the model beneath the actions.</span></li>
              </ol>
              <div className="visible-count">
                <strong>{filteredActions.length}</strong>
                <span>actions currently visible</span>
              </div>
            </>
          )}
        </aside>
      </section>

      <MomentumChart analytics={analytics} onSelectAction={setSelectedAction} />
    </>
  );
}

export default function App() {
  const [view, setView] = useState<View>("match");
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [selectedMatchId, setSelectedMatchId] = useState<number>(SHOWCASE_MATCH_ID);
  const [surface, setSurface] = useState<ThreatSurface | null>(null);
  const [analytics, setAnalytics] = useState<MatchAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requestKey, setRequestKey] = useState(0);
  const [primerOpen, setPrimerOpen] = useState(readPrimerOpen);

  const setGuideOpen = (open: boolean) => {
    setPrimerOpen(open);
    persistPrimerOpen(open);
  };

  useEffect(() => {
    const controller = new AbortController();
    fetchMatches(controller.signal)
      .then((catalogue) => {
        setMatches(catalogue);
        setSelectedMatchId((current) => {
          if (catalogue.some((match) => match.match_id === current)) return current;
          return catalogue[0]?.match_id ?? current;
        });
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "Unable to load the match list");
      });
    return () => controller.abort();
  }, [requestKey]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchSurface(controller.signal),
      fetchMatchAnalytics(selectedMatchId, controller.signal),
    ])
      .then(([surfaceResponse, analyticsResponse]) => {
        setSurface(surfaceResponse);
        setAnalytics(analyticsResponse);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "Unable to load match analytics");
      });
    return () => controller.abort();
  }, [requestKey, selectedMatchId]);

  return (
    <div className="app-shell">
      <header className="site-header">
        <button className="brand" onClick={() => setView("match")}>
          <span className="brand-mark">xT</span>
          <span>
            <strong>Touchline</strong>
            <small>Expected Threat Explorer</small>
          </span>
        </button>
        <nav aria-label="Explorer sections">
          {(["match", "players", "model"] as View[]).map((item) => (
            <button
              key={item}
              className={view === item ? "nav-button is-active" : "nav-button"}
              onClick={() => setView(item)}
            >
              {item === "match" ? "Match" : item === "players" ? "Players" : "The model"}
            </button>
          ))}
        </nav>
        <div className="header-tools">
          <label className="match-switcher">
            <span>Match</span>
            <select
              value={selectedMatchId}
              onChange={(event) => {
                setSelectedMatchId(Number(event.target.value));
              }}
              disabled={matches.length === 0}
              aria-label="Select a match to explore"
            >
              {matches.map((match) => (
                <option key={match.match_id} value={match.match_id}>
                  {match.home_team} v {match.away_team}
                  {match.home_score != null && match.away_score != null
                    ? ` ${match.home_score}–${match.away_score}`
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="model-badge">
            <i />
            <span>{surface ? `${surface.model_id} · ${surface.training_matches} matches` : "Loading model"}</span>
          </div>
        </div>
      </header>

      <main>
        {error ? (
          <section className="state-card panel">
            <span className="state-icon">!</span>
            <h1>We could not load the match</h1>
            <p>{error}</p>
            <button
              className="primary-button"
              onClick={() => {
                setError(null);
                setRequestKey((key) => key + 1);
              }}
            >
              Try again
            </button>
          </section>
        ) : !surface || !analytics ? (
          <section className="state-card panel">
            <span className="loading-ball" />
            <h1>Building the match story</h1>
            <p>Fitting the xT surface and scoring every on-ball action…</p>
          </section>
        ) : view === "match" ? (
          <MatchView
            analytics={analytics}
            surface={surface}
            primerOpen={primerOpen}
            onPrimerOpenChange={setGuideOpen}
          />
        ) : view === "players" ? (
          <PlayerView analytics={analytics} />
        ) : (
          <ModelView surface={surface} />
        )}
      </main>

      <footer>
        <span>Built with StatsBomb Open Data</span>
        <span>Karun Singh-style Expected Threat · 16×12 grid</span>
      </footer>
    </div>
  );
}