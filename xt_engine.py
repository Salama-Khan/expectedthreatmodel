"""Expected Threat (xT) mathematical engine.

I solve Karun Singh's linear system over a StatsBomb grid:

    xT = (s * g) + (m_continue * T @ xT)
    (I - P) xT = b

where failed passes/carries leak probability to 0 instead of inheriting destination xT.
"""

from __future__ import annotations

import logging
from typing import Mapping

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0
DEFAULT_GRID_COLUMNS = 16
DEFAULT_GRID_ROWS = 12

FloatArray = npt.NDArray[np.floating]
ArrayLike = npt.ArrayLike
Table = Mapping[str, ArrayLike]


def n_zones(
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> int:
    return _positive_int("grid_columns", grid_columns) * _positive_int(
        "grid_rows", grid_rows
    )


def _positive_int(name: str, value: int) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"I need {name} to be a positive integer, got {value}")
    return parsed


def zone_index_sql(
    x_col: str,
    y_col: str,
    *,
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> str:
    """I bin a StatsBomb (x, y) pair into a row-major zone index.

    I clamp the last bin so x=120 / y=80 do not overflow the grid. I keep x along
    the attacking axis so higher column indices are closer to the opponent goal.
    """
    cols = _positive_int("grid_columns", grid_columns)
    rows = _positive_int("grid_rows", grid_rows)
    return (
        f"(LEAST(TRUNC({y_col} * {rows} / {PITCH_WIDTH}), {rows - 1}) * {cols})"
        f" + LEAST(TRUNC({x_col} * {cols} / {PITCH_LENGTH}), {cols - 1})"
    )


def shots_query(
    *,
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> str:
    # I drop shots with no start location so I never GROUP BY a NULL zone.
    zone = zone_index_sql(
        "location_x", "location_y", grid_columns=grid_columns, grid_rows=grid_rows
    )
    return f"""
SELECT
    {zone} AS zone_index,
    COUNT(*) AS shot_attempts,
    SUM(CASE WHEN outcome = 'Goal' THEN 1 ELSE 0 END) AS goals
FROM events
WHERE type_name = 'Shot'
  AND location_x IS NOT NULL
  AND location_y IS NOT NULL
GROUP BY zone_index
"""


def moves_query(
    *,
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> str:
    # I count every pass/carry as a move attempt, and only implicit-success
    # passes plus all carries as successful moves. Failed passes must not feed T.
    zone = zone_index_sql(
        "location_x", "location_y", grid_columns=grid_columns, grid_rows=grid_rows
    )
    return f"""
SELECT
    {zone} AS zone_index,
    COUNT(*) AS move_attempts,
    SUM(CASE
        WHEN type_name = 'Carry' THEN 1
        WHEN type_name = 'Pass' AND outcome IS NULL THEN 1
        ELSE 0
    END) AS successful_moves
FROM events
WHERE type_name IN ('Pass', 'Carry')
  AND location_x IS NOT NULL
  AND location_y IS NOT NULL
GROUP BY zone_index
"""


def transitions_query(
    *,
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> str:
    # I only walk successful on-ball moves. Incomplete passes are turnovers, so
    # they must not create a start->end edge in T.
    origin = zone_index_sql(
        "location_x", "location_y", grid_columns=grid_columns, grid_rows=grid_rows
    )
    dest = zone_index_sql(
        "end_location_x",
        "end_location_y",
        grid_columns=grid_columns,
        grid_rows=grid_rows,
    )
    return f"""
SELECT
    {origin} AS origin_zone,
    {dest} AS dest_zone,
    COUNT(*) AS n
FROM events
WHERE (
        type_name = 'Carry'
        OR (type_name = 'Pass' AND outcome IS NULL)
    )
  AND location_x IS NOT NULL
  AND location_y IS NOT NULL
  AND end_location_x IS NOT NULL
  AND end_location_y IS NOT NULL
GROUP BY origin_zone, dest_zone
"""


# Keep 16x12 query constants for psql while the builders stay parameterized.
SHOTS_QUERY = shots_query()
MOVES_QUERY = moves_query()
TRANSITIONS_QUERY = transitions_query()


def calculate_base_probabilities(
    shots: int,
    goals: int,
    moves: int,
    successful_moves: int,
) -> dict[str, float]:
    """I compute per-zone action probabilities, including move success.

    s: P(shoot | action)
    g: P(goal | shot)
    m: P(attempt a move | action)
    r: P(successful move | move attempt)
    m_continue: P(successful move | action) = m * r

    I multiply T by m_continue, not m. Using m would treat a turnover as if the
    ball still arrived in the destination zone.
    """
    shots_f = float(shots)
    goals_f = float(goals)
    moves_f = float(moves)
    successful_f = float(successful_moves)
    actions = shots_f + moves_f

    s = shots_f / actions if actions else 0.0
    g = goals_f / shots_f if shots_f else 0.0
    m = moves_f / actions if actions else 0.0
    r = successful_f / moves_f if moves_f else 0.0
    return {
        "s": s,
        "g": g,
        "m": m,
        "r": r,
        "m_continue": m * r,
    }


def densify_zone_values(
    zone_index: ArrayLike,
    values: ArrayLike,
    *,
    n_cells: int,
) -> FloatArray:
    """I expand a sparse GROUP BY result into a dense length-n_cells vector.

    Zones with no events stay 0, which is the correct prior: no evidence of
    shooting or moving from that cell.
    """
    dense = np.zeros(n_cells, dtype=np.float64)
    zones = np.asarray(zone_index, dtype=np.int64)
    amounts = np.asarray(values, dtype=np.float64)
    if zones.size != amounts.size:
        raise ValueError("I need zone_index and values to have the same length")
    if zones.size == 0:
        return dense
    if np.any(zones < 0) or np.any(zones >= n_cells):
        raise ValueError("I received a zone_index outside the configured grid")
    np.add.at(dense, zones, amounts)
    return dense


def zone_probabilities(
    shot_attempts: FloatArray,
    goals: FloatArray,
    move_attempts: FloatArray,
    successful_moves: FloatArray,
) -> dict[str, FloatArray]:
    """I vectorise calculate_base_probabilities over every grid cell."""
    shots = np.asarray(shot_attempts, dtype=np.float64)
    goals_v = np.asarray(goals, dtype=np.float64)
    moves = np.asarray(move_attempts, dtype=np.float64)
    success = np.asarray(successful_moves, dtype=np.float64)
    if not (shots.shape == goals_v.shape == moves.shape == success.shape):
        raise ValueError("I need shot, goal, move, and success vectors to share one shape")

    actions = shots + moves
    s = np.divide(shots, actions, out=np.zeros_like(actions), where=actions != 0)
    g = np.divide(goals_v, shots, out=np.zeros_like(actions), where=shots != 0)
    m = np.divide(moves, actions, out=np.zeros_like(actions), where=actions != 0)
    r = np.divide(success, moves, out=np.zeros_like(actions), where=moves != 0)
    return {
        "s": s,
        "g": g,
        "m": m,
        "r": r,
        "m_continue": m * r,
    }


def build_transition_matrix(
    origin_zone: ArrayLike,
    dest_zone: ArrayLike,
    counts: ArrayLike,
    successful_moves: FloatArray,
    *,
    n_cells: int,
) -> FloatArray:
    """I build T where T[i, j] = P(end in j | successful move from i).

    Rows with no successful moves stay all zeros. Those zones contribute no
    continuation mass, which is what I want for empty or turnover-only cells.
    """
    T_counts = np.zeros((n_cells, n_cells), dtype=np.float64)
    origins = np.asarray(origin_zone, dtype=np.int64)
    dests = np.asarray(dest_zone, dtype=np.int64)
    weights = np.asarray(counts, dtype=np.float64)
    if not (origins.size == dests.size == weights.size):
        raise ValueError("I need origin, dest, and count arrays to have the same length")
    if origins.size:
        if (
            np.any(origins < 0)
            or np.any(origins >= n_cells)
            or np.any(dests < 0)
            or np.any(dests >= n_cells)
        ):
            raise ValueError("I received a transition zone outside the configured grid")
        np.add.at(T_counts, (origins, dests), weights)

    denom = np.asarray(successful_moves, dtype=np.float64).reshape(-1, 1)
    if denom.shape[0] != n_cells:
        raise ValueError("I need successful_moves to have one entry per zone")
    T = np.zeros_like(T_counts)
    np.divide(T_counts, denom, out=T, where=denom != 0)
    return T


def solve_xt(
    s: ArrayLike,
    g: ArrayLike,
    m_continue: ArrayLike,
    T: ArrayLike,
) -> FloatArray:
    """I solve (I - P) xT = b with P = m_continue ⊙ T and b = s ⊙ g.

    m_continue must be P(successful move | action), not P(attempted move).
    T rows should sum to 1 for zones that had at least one successful move.
    """
    s_v = np.asarray(s, dtype=np.float64).reshape(-1)
    g_v = np.asarray(g, dtype=np.float64).reshape(-1)
    c_v = np.asarray(m_continue, dtype=np.float64).reshape(-1)
    T_m = np.asarray(T, dtype=np.float64)
    n_cells = s_v.shape[0]
    if g_v.shape != (n_cells,) or c_v.shape != (n_cells,):
        raise ValueError("I need s, g, and m_continue to be vectors of equal length")
    if T_m.shape != (n_cells, n_cells):
        raise ValueError(
            f"I need T to be shape {(n_cells, n_cells)}, got {T_m.shape}"
        )

    b = s_v * g_v
    P = c_v.reshape(-1, 1) * T_m
    system = np.eye(n_cells) - P
    try:
        xt_surface = np.linalg.solve(system, b)
    except np.linalg.LinAlgError as exc:
        raise RuntimeError(
            "I could not invert (I - P). A subset of zones is a closed successful-move "
            "loop with no shooting, so expected threat is not identified."
        ) from exc

    # I clip tiny solver noise so later event_threat rows satisfy 0 <= xT <= 1.
    overshoot = float(np.max(xt_surface) - 1.0) if xt_surface.size else 0.0
    undershoot = float(-np.min(xt_surface)) if xt_surface.size else 0.0
    if overshoot > 1e-8 or undershoot > 1e-8:
        logger.warning(
            "I clipped xT values that left [0, 1] by up to %.3e",
            max(overshoot, undershoot),
        )
    return np.clip(xt_surface, 0.0, 1.0)


def _column(table: Table, name: str) -> np.ndarray:
    if name not in table:
        raise KeyError(f"I need column {name!r} on the aggregated xT table")
    return np.asarray(table[name])


def fit_xt_surface(
    shots: Table,
    moves: Table,
    transitions: Table,
    *,
    grid_columns: int = DEFAULT_GRID_COLUMNS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> FloatArray:
    """I densify sparse SQL aggregates and return an xT value per zone."""
    n_cells = n_zones(grid_columns, grid_rows)
    shot_attempts = densify_zone_values(
        _column(shots, "zone_index"),
        _column(shots, "shot_attempts"),
        n_cells=n_cells,
    )
    goals = densify_zone_values(
        _column(shots, "zone_index"),
        _column(shots, "goals"),
        n_cells=n_cells,
    )
    move_attempts = densify_zone_values(
        _column(moves, "zone_index"),
        _column(moves, "move_attempts"),
        n_cells=n_cells,
    )
    successful_moves = densify_zone_values(
        _column(moves, "zone_index"),
        _column(moves, "successful_moves"),
        n_cells=n_cells,
    )
    probs = zone_probabilities(shot_attempts, goals, move_attempts, successful_moves)
    T = build_transition_matrix(
        _column(transitions, "origin_zone"),
        _column(transitions, "dest_zone"),
        _column(transitions, "n"),
        successful_moves,
        n_cells=n_cells,
    )
    return solve_xt(probs["s"], probs["g"], probs["m_continue"], T)
