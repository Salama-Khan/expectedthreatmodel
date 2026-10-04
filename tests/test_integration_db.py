from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import psycopg

from run_pipeline import run_pipeline                    # NEW

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
MATCH_PATH = "data/15946.json"                            # NEW

ALL_TABLES = (
    "event_threat, quarantined_events, events, xt_models, match_players, "
    "match_teams, players, teams, matches, seasons, competitions, ingestion_runs"
)


@unittest.skipUnless(TEST_DATABASE_URL, "set TEST_DATABASE_URL to run database tests")
class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        # I start every test from empty tables so counts only reflect this test.
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(f"TRUNCATE {ALL_TABLES} CASCADE")

    def count(self, sql: str, *params: object) -> int:
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_off_pitch_event_is_quarantined_and_counted(self) -> None:
        events = json.loads(Path(MATCH_PATH).read_text())
        bad_event = events[4]
        bad_event["location"] = [999.0, 40.1]

        # I load a temporary copy so the real data file is never modified.
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "15946.json"
            bad_path.write_text(json.dumps(events))
            shutil.copy("data/15946_lineups.json", tmp)
            run_pipeline(bad_path, database_url=TEST_DATABASE_URL, data_dir="data")

        quarantined = self.count(
            "SELECT count(*) FROM quarantined_events WHERE event_id = %s::uuid",
            bad_event["id"],
        )
        loaded = self.count("SELECT count(*) FROM events WHERE match_id = %s", 15946)
        run_quarantined = self.count(
            "SELECT quarantined_count FROM ingestion_runs "
            "WHERE source_uri LIKE %s ORDER BY started_at DESC LIMIT 1",
            "%/15946.json",
        )

        self.assertEqual(quarantined, 1)
        self.assertEqual(loaded, 3761)
        self.assertEqual(run_quarantined, 1)