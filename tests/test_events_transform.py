from __future__ import annotations

import unittest

import polars as pl

from events_transform import EVENTS_SCHEMA, OFF_PITCH_REASON, partition_off_pitch


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "00000000-0000-0000-0000-0000000000aa",
        "match_id": 15946,
        "team_id": 1,
        "event_index": 0,
        "possession_id": 1,
        "period": 1,
        "minute": 1,
        "second": 0.5,
        "type_name": "Pass",
        "outcome": None,
        "actor_player_id": 10,
        "recipient_player_id": 11,
        "location_x": 40.0,
        "location_y": 40.0,
        "end_location_x": 50.0,
        "end_location_y": 40.0,
    }
    row.update(overrides)
    return row


def _frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).select(
        pl.col(name).cast(dtype, strict=False).alias(name)
        for name, dtype in EVENTS_SCHEMA.items()
    )


class PartitionOffPitchTests(unittest.TestCase):
    def test_off_pitch_x_is_rejected_and_on_pitch_row_is_kept(self) -> None:
        kept, rejected = partition_off_pitch(
            _frame(
                [
                    _row(),
                    _row(
                        event_id="00000000-0000-0000-0000-000000000002",
                        event_index=4,
                        location_x=-2.5,
                    ),
                ]
            )
        )

        self.assertEqual(kept.height, 1)
        self.assertEqual(kept["event_index"].to_list(), [0])
        self.assertEqual(rejected.height, 1)
        self.assertEqual(rejected["location_x"].to_list(), [-2.5])
        self.assertEqual(rejected["event_index"].to_list(), [4])
        self.assertEqual(rejected["reason"].to_list(), [OFF_PITCH_REASON])

    def test_null_location_stays_in_events(self) -> None:
        kept, rejected = partition_off_pitch(
            _frame(
                [
                    _row(
                        type_name="Starting XI",
                        outcome=None,
                        actor_player_id=None,
                        recipient_player_id=None,
                        location_x=None,
                        location_y=None,
                        end_location_x=None,
                        end_location_y=None,
                    )
                ]
            )
        )
        self.assertEqual(kept.height, 1)
        self.assertEqual(rejected.height, 0)


if __name__ == "__main__":
    unittest.main()
