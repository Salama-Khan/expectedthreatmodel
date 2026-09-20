import { useMemo, useState } from "react";

import type { MatchAction, MatchTeam, ThreatSurface, ZoneThreat } from "./api";

const LENGTH = 120;
const WIDTH = 80;
const GOAL_DEPTH = 2.5;
const LINE = "rgba(240, 247, 242, 0.82)";

type HoverTarget =
  | { kind: "zone"; zone: ZoneThreat; x: number; y: number }
  | { kind: "action"; action: MatchAction; x: number; y: number };

type InteractivePitchProps = {
  surface: ThreatSurface;
  actions?: MatchAction[];
  teams?: MatchTeam[];
  mode: "actions" | "surface";
  showHeatmap?: boolean;
  selectedActionId?: string | null;
  onSelectAction?: (action: MatchAction) => void;
};

function interpolate(
  start: [number, number, number],
  end: [number, number, number],
  amount: number,
): [number, number, number] {
  return start.map((value, index) =>
    Math.round(value + (end[index] - value) * amount),
  ) as [number, number, number];
}

function heatColour(value: number, maxValue: number, alpha = 0.8): string {
  const t = maxValue > 0 ? Math.min(1, Math.max(0, value / maxValue)) : 0;
  const low: [number, number, number] = [68, 1, 84];
  const middle: [number, number, number] = [33, 145, 140];
  const high: [number, number, number] = [253, 231, 37];
  const [r, g, b] =
    t < 0.5
      ? interpolate(low, middle, t * 2)
      : interpolate(middle, high, (t - 0.5) * 2);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0.16, alpha * (0.35 + t * 0.65))})`;
}

function PitchMarkings() {
  const mark = {
    fill: "none",
    stroke: LINE,
    strokeWidth: 0.34,
    vectorEffect: "non-scaling-stroke" as const,
  };
  return (
    <g aria-hidden="true">
      <rect x={0} y={0} width={120} height={80} {...mark} />
      <line x1={60} y1={0} x2={60} y2={80} {...mark} />
      <circle cx={60} cy={40} r={10} {...mark} />
      <circle cx={60} cy={40} r={0.5} fill={LINE} />
      <rect x={0} y={18} width={18} height={44} {...mark} />
      <rect x={102} y={18} width={18} height={44} {...mark} />
      <rect x={0} y={30} width={6} height={20} {...mark} />
      <rect x={114} y={30} width={6} height={20} {...mark} />
      <circle cx={12} cy={40} r={0.45} fill={LINE} />
      <circle cx={108} cy={40} r={0.45} fill={LINE} />
      <path d="M 18 32 A 10 10 0 0 0 18 48" {...mark} />
      <path d="M 102 32 A 10 10 0 0 1 102 48" {...mark} />
      <path d="M 1 0 A 1 1 0 0 0 0 1" {...mark} />
      <path d="M 0 79 A 1 1 0 0 0 1 80" {...mark} />
      <path d="M 119 0 A 1 1 0 0 1 120 1" {...mark} />
      <path d="M 120 79 A 1 1 0 0 1 119 80" {...mark} />
      <rect x={-GOAL_DEPTH} y={36} width={GOAL_DEPTH} height={8} {...mark} />
      <rect x={120} y={36} width={GOAL_DEPTH} height={8} {...mark} />
    </g>
  );
}

function signed(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

function Tooltip({ target }: { target: HoverTarget }) {
  const style = {
    left: `${Math.min(88, Math.max(12, (target.x / LENGTH) * 100))}%`,
    top: `${Math.min(82, Math.max(12, (target.y / WIDTH) * 100))}%`,
  };
  if (target.kind === "zone") {
    return (
      <div className="pitch-tooltip" style={style}>
        <span>Zone {target.zone.zone_index}</span>
        <strong>{target.zone.xt_value.toFixed(3)} xT</strong>
      </div>
    );
  }
  return (
    <div className="pitch-tooltip" style={style}>
      <span>
        {target.action.minute}&apos; ·{" "}
        {target.action.player_name ?? target.action.team_name}
      </span>
      <strong>
        {target.action.action_type} {signed(target.action.delta_xt)} xT
      </strong>
    </div>
  );
}

export default function InteractivePitch({
  surface,
  actions = [],
  teams = [],
  mode,
  showHeatmap = false,
  selectedActionId,
  onSelectAction,
}: InteractivePitchProps) {
  const [hovered, setHovered] = useState<HoverTarget | null>(null);
  const maxXt = useMemo(
    () => Math.max(...surface.zones.map((zone) => zone.xt_value), 0),
    [surface.zones],
  );
  const teamColours = useMemo(
    () =>
      new Map(
        teams.map((team) => [
          team.team_id,
          team.side === "home" ? "#43d7c4" : "#ff9f5a",
        ]),
      ),
    [teams],
  );
  const cellWidth = LENGTH / surface.grid_columns;
  const cellHeight = WIDTH / surface.grid_rows;
  const displaySurface = mode === "surface" || showHeatmap;

  return (
    <div className="pitch-frame">
      <div className="pitch-direction">
        <span>Normalized attacking direction</span>
        <strong>→</strong>
      </div>
      <div className="pitch-stage">
        <svg
          viewBox={`${-GOAL_DEPTH - 1} -1 ${LENGTH + GOAL_DEPTH * 2 + 2} 82`}
          role="img"
          aria-label={
            mode === "surface"
              ? "Expected Threat model surface"
              : "Interactive match action map"
          }
          className="football-pitch"
        >
          <rect x={0} y={0} width={LENGTH} height={WIDTH} className="pitch-grass" />
          {displaySurface &&
            surface.zones.map((zone) => {
              const column = zone.zone_index % surface.grid_columns;
              const row = Math.floor(zone.zone_index / surface.grid_columns);
              const x = column * cellWidth;
              const y = row * cellHeight;
              const target: HoverTarget = {
                kind: "zone",
                zone,
                x: x + cellWidth / 2,
                y: y + cellHeight / 2,
              };
              return (
                <rect
                  key={zone.zone_index}
                  x={x}
                  y={y}
                  width={cellWidth}
                  height={cellHeight}
                  fill={heatColour(
                    zone.xt_value,
                    maxXt,
                    mode === "surface" ? 0.9 : 0.42,
                  )}
                  className="threat-zone"
                  role="button"
                  tabIndex={mode === "surface" ? 0 : -1}
                  aria-label={`Zone ${zone.zone_index}, ${zone.xt_value.toFixed(3)} expected threat`}
                  onMouseEnter={() => setHovered(target)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(target)}
                  onBlur={() => setHovered(null)}
                />
              );
            })}
          {mode === "actions" &&
            actions.map((action) => {
              const colour = teamColours.get(action.team_id) ?? "#ffffff";
              const selected = action.event_id === selectedActionId;
              const hasEnd =
                action.end_location_x !== null && action.end_location_y !== null;
              const target: HoverTarget = {
                kind: "action",
                action,
                x: action.end_location_x ?? action.location_x,
                y: action.end_location_y ?? action.location_y,
              };
              return (
                <g
                  key={action.event_id}
                  className={`pitch-action${selected ? " is-selected" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${action.action_type} by ${action.player_name ?? action.team_name} at ${action.minute} minutes`}
                  onMouseEnter={() => setHovered(target)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(target)}
                  onBlur={() => setHovered(null)}
                  onClick={() => onSelectAction?.(action)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectAction?.(action);
                    }
                  }}
                >
                  {hasEnd && (
                    <line
                      x1={action.location_x}
                      y1={action.location_y}
                      x2={action.end_location_x ?? action.location_x}
                      y2={action.end_location_y ?? action.location_y}
                      stroke={colour}
                      strokeWidth={
                        selected
                          ? 0.85
                          : 0.2 + Math.min(Math.abs(action.delta_xt ?? 0) * 8, 0.45)
                      }
                      strokeDasharray={
                        action.action_type === "Carry" ? "1.2 0.8" : undefined
                      }
                      opacity={(action.delta_xt ?? 0) >= 0 ? 0.75 : 0.32}
                      vectorEffect="non-scaling-stroke"
                    />
                  )}
                  <circle
                    cx={action.end_location_x ?? action.location_x}
                    cy={action.end_location_y ?? action.location_y}
                    r={action.action_type === "Shot" ? 0.9 : selected ? 0.72 : 0.38}
                    fill={action.action_type === "Shot" ? "#f8e16c" : colour}
                    stroke={selected ? "#ffffff" : "rgba(8, 20, 17, 0.7)"}
                    strokeWidth={selected ? 0.35 : 0.15}
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              );
            })}
          <PitchMarkings />
        </svg>
        {hovered && <Tooltip target={hovered} />}
      </div>
      {mode === "surface" ? (
        <div className="heat-legend">
          <span>Lower threat</span>
          <div className="heat-legend__bar" />
          <span>{maxXt.toFixed(3)} xT</span>
        </div>
      ) : (
        <div className="team-legend">
          {teams.map((team) => (
            <span key={team.team_id}>
              <i
                className={team.side === "home" ? "legend-home" : "legend-away"}
              />
              {team.team_name}
            </span>
          ))}
          <span className="team-legend__hint">Select an action for detail</span>
        </div>
      )}
    </div>
  );
}
