from __future__ import annotations

import unittest

import polars as pl

from events_loader import drop_known_event_ids


class DropKnownEventIdsTests(unittest.TestCase):
    def test_second_load_keeps_only_new_event_ids(self) -> None:
        events = pl.DataFrame(
            {
                "event_id": [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ]
            }
        )
        kept = drop_known_event_ids(
            events,
            {"00000000-0000-0000-0000-000000000001"},
        )
        self.assertEqual(
            kept["event_id"].to_list(),
            ["00000000-0000-0000-0000-000000000002"],
        )


if __name__ == "__main__":
    unittest.main()
