"""Pure helpers for assigning a fitted xT surface to match actions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0


def zone_index(
    x: float,
    y: float,
    *,
    grid_columns: int,
    grid_rows: int,
) -> int:
    """Return the row-major xT zone for a StatsBomb coordinate."""
    if not (0.0 <= x <= PITCH_LENGTH and 0.0 <= y <= PITCH_WIDTH):
        raise ValueError(f"coordinate ({x}, {y}) is outside the StatsBomb pitch")
    column = min(int(x * grid_columns / PITCH_LENGTH), grid_columns - 1)
    row = min(int(y * grid_rows / PITCH_WIDTH), grid_rows - 1)
    return row * grid_columns + column


def end_zone_xt(
    action_type: str,
    outcome: object,
    surface: Sequence[float],
    end_x: object,
    end_y: object,
    *,
    grid_columns: int,
    grid_rows: int,
) -> float | None:
    """xT at the end of an on-ball action.

    A pass with any outcome has already failed, so it leaks to 0 and does not
    read the destination zone. Successful passes and carries use that zone.
    Shots have no movement value.
    """
    if action_type == "Pass" and outcome is not None:
        return 0.0
    if action_type in {"Pass", "Carry"} and end_x is not None and end_y is not None:
        return float(
            surface[
                zone_index(
                    float(end_x),
                    float(end_y),
                    grid_columns=grid_columns,
                    grid_rows=grid_rows,
                )
            ]
        )
    return None


def score_on_ball_events(
    rows: Sequence[Mapping[str, Any]],
    surface: Sequence[float],
    *,
    grid_columns: int,
    grid_rows: int,
) -> list[dict[str, float | str]]:
    """Score events-table rows to xt_start and xt_end.

    Only passes and carries are returned. delta_xt is omitted because
    event_threat generates it as xt_end - xt_start.
    """
    expected_cells = grid_columns * grid_rows
    if len(surface) != expected_cells:
        raise ValueError(
            f"surface has {len(surface)} cells; expected {expected_cells}"
        )

    scored: list[dict[str, float | str]] = []
    for row in rows:
        action_type = str(row.get("type_name") or row.get("action_type") or "")
        end_xt = end_zone_xt(
            action_type,
            row.get("outcome"),
            surface,
            row.get("end_location_x"),
            row.get("end_location_y"),
            grid_columns=grid_columns,
            grid_rows=grid_rows,
        )
        if end_xt is None:
            continue
        start_x = row.get("location_x")
        start_y = row.get("location_y")
        if start_x is None or start_y is None:
            continue
        start_xt = float(
            surface[
                zone_index(
                    float(start_x),
                    float(start_y),
                    grid_columns=grid_columns,
                    grid_rows=grid_rows,
                )
            ]
        )
        scored.append(
            {
                "event_id": str(row["event_id"]),
                "xt_start": start_xt,
                "xt_end": end_xt,
            }
        )
    return scored


def score_match_actions(
    rows: Sequence[Mapping[str, Any]],
    surface: Sequence[float],
    *,
    grid_columns: int,
    grid_rows: int,
) -> list[dict[str, Any]]:
    """Attach start/end xT to pass, carry, and shot rows.

    Successful passes and carries receive end-zone minus start-zone xT. Failed
    passes lose the threat present at their start zone. Shots are shown on the
    action map but are not assigned a movement delta.
    """
    expected_cells = grid_columns * grid_rows
    if len(surface) != expected_cells:
        raise ValueError(
            f"surface has {len(surface)} cells; expected {expected_cells}"
        )

    scored: list[dict[str, Any]] = []
    for row in rows:
        start_x = float(row["location_x"])
        start_y = float(row["location_y"])
        start_xt = float(
            surface[
                zone_index(
                    start_x,
                    start_y,
                    grid_columns=grid_columns,
                    grid_rows=grid_rows,
                )
            ]
        )

        action_type = str(row["action_type"])
        end_xt = end_zone_xt(
            action_type,
            row.get("outcome"),
            surface,
            row.get("end_location_x"),
            row.get("end_location_y"),
            grid_columns=grid_columns,
            grid_rows=grid_rows,
        )
        delta_xt = None if end_xt is None else end_xt - start_xt

        scored.append(
            {
                **dict(row),
                "event_id": str(row["event_id"]),
                "xt_start": start_xt,
                "xt_end": end_xt,
                "delta_xt": delta_xt,
            }
        )
    return scored


def player_impacts(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate movement xT by player for a leaderboard."""
    buckets: dict[int, dict[str, Any]] = {}
    for action in actions:
        player_id = action.get("player_id")
        delta = action.get("delta_xt")
        if player_id is None or delta is None:
            continue
        parsed_id = int(player_id)
        bucket = buckets.setdefault(
            parsed_id,
            {
                "player_id": parsed_id,
                "player_name": action.get("player_name") or f"Player {parsed_id}",
                "team_id": int(action["team_id"]),
                "team_name": str(action["team_name"]),
                "actions": 0,
                "positive_xt": 0.0,
                "net_xt": 0.0,
                "best_action_xt": 0.0,
            },
        )
        value = float(delta)
        bucket["actions"] += 1
        bucket["positive_xt"] += max(value, 0.0)
        bucket["net_xt"] += value
        bucket["best_action_xt"] = max(bucket["best_action_xt"], value)

    return sorted(
        buckets.values(),
        key=lambda player: (player["positive_xt"], player["net_xt"]),
        reverse=True,
    )


def team_threat_summaries(
    actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate movement value and shots for each team."""
    buckets: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "team_id": 0,
            "team_name": "",
            "positive_xt": 0.0,
            "net_xt": 0.0,
            "actions": 0,
            "shots": 0,
        }
    )
    for action in actions:
        team_id = int(action["team_id"])
        bucket = buckets[team_id]
        bucket["team_id"] = team_id
        bucket["team_name"] = str(action["team_name"])
        if action["action_type"] == "Shot":
            bucket["shots"] += 1
        delta = action.get("delta_xt")
        if delta is not None:
            value = float(delta)
            bucket["actions"] += 1
            bucket["positive_xt"] += max(value, 0.0)
            bucket["net_xt"] += value
    return sorted(buckets.values(), key=lambda team: team["team_id"])
