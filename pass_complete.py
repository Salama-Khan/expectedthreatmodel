"""Pass-complete classifier. Split by match_id so events from one game stay together."""

from __future__ import annotations

import os
import sys

import pandas as pd
import psycopg
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from match_analytics import zone_index

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres@localhost:5432/footballanalysis"
)
GRID_COLUMNS = 16
GRID_ROWS = 12


def _zone(x: float, y: float) -> int:
    return zone_index(x, y, grid_columns=GRID_COLUMNS, grid_rows=GRID_ROWS)


def load_passes(conn: psycopg.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT match_id, outcome, location_x, location_y,
               end_location_x, end_location_y
        FROM events
        WHERE type_name = 'Pass'
          AND location_x IS NOT NULL AND location_y IS NOT NULL
          AND end_location_x IS NOT NULL AND end_location_y IS NOT NULL
        """
    ).fetchall()
    frame = pd.DataFrame(
        rows,
        columns=["match_id", "outcome", "x", "y", "end_x", "end_y"],
    )
    frame["complete"] = frame["outcome"].isna().astype(int)
    frame["start_zone"] = [
        _zone(float(x), float(y)) for x, y in zip(frame["x"], frame["y"])
    ]
    frame["end_zone"] = [
        _zone(float(x), float(y)) for x, y in zip(frame["end_x"], frame["end_y"])
    ]
    return frame


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        frame = load_passes(conn)

    if frame["match_id"].nunique() < 2:
        sys.exit("Need at least two matches. Run ingest_open_data.py --limit 8")

    features = frame[["start_zone", "end_zone"]]
    target = frame["complete"]
    groups = frame["match_id"]
    train_idx, test_idx = next(
        GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=0).split(
            features, target, groups
        )
    )

    pipe = Pipeline(
        [
            (
                "encode",
                ColumnTransformer(
                    [("zones", OneHotEncoder(handle_unknown="ignore"), ["start_zone", "end_zone"])]
                ),
            ),
            ("model", LogisticRegression(max_iter=500)),
        ]
    )
    pipe.fit(features.iloc[train_idx], target.iloc[train_idx])
    predicted = pipe.predict(features.iloc[test_idx])
    print("held-out matches:", sorted(groups.iloc[test_idx].unique().tolist()))
    print(confusion_matrix(target.iloc[test_idx], predicted))
    print(classification_report(target.iloc[test_idx], predicted, digits=3))


if __name__ == "__main__":
    main()