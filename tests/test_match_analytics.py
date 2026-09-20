from __future__ import annotations

import unittest

from api_schemas import MatchAction, MatchAnalyticsResponse, MatchTeam
from match_analytics import player_impacts, score_match_actions, zone_index


class ZoneIndexTests(unittest.TestCase):
    def test_clamps_pitch_boundaries_into_last_zone(self) -> None:
        self.assertEqual(
            zone_index(120.0, 80.0, grid_columns=16, grid_rows=12),
            191,
        )

    def test_rejects_coordinates_outside_pitch(self) -> None:
        with self.assertRaises(ValueError):
            zone_index(121.0, 40.0, grid_columns=16, grid_rows=12)


class MatchScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.surface = [index / 100.0 for index in range(4)]
        self.rows = [
            {
                "event_id": "00000000-0000-0000-0000-000000000001",
                "event_index": 1,
                "minute": 10,
                "second": 1.2,
                "team_id": 1,
                "team_name": "Home",
                "player_id": 11,
                "player_name": "Creator",
                "action_type": "Pass",
                "outcome": None,
                "location_x": 10.0,
                "location_y": 10.0,
                "end_location_x": 80.0,
                "end_location_y": 10.0,
            },
            {
                "event_id": "00000000-0000-0000-0000-000000000002",
                "event_index": 2,
                "minute": 11,
                "second": 2.4,
                "team_id": 1,
                "team_name": "Home",
                "player_id": 11,
                "player_name": "Creator",
                "action_type": "Pass",
                "outcome": "Incomplete",
                "location_x": 80.0,
                "location_y": 10.0,
                "end_location_x": 100.0,
                "end_location_y": 10.0,
            },
        ]

    def test_scores_completed_and_failed_passes(self) -> None:
        actions = score_match_actions(
            self.rows,
            self.surface,
            grid_columns=2,
            grid_rows=2,
        )
        self.assertAlmostEqual(actions[0]["delta_xt"], 0.01)
        self.assertAlmostEqual(actions[1]["delta_xt"], -0.01)
        self.assertEqual(actions[1]["xt_end"], 0.0)

    def test_player_impact_separates_positive_and_net_value(self) -> None:
        actions = score_match_actions(
            self.rows,
            self.surface,
            grid_columns=2,
            grid_rows=2,
        )
        impact = player_impacts(actions)[0]
        self.assertEqual(impact["actions"], 2)
        self.assertAlmostEqual(impact["positive_xt"], 0.01)
        self.assertAlmostEqual(impact["net_xt"], 0.0)


class ApiContractTests(unittest.TestCase):
    def test_match_response_contract_accepts_scored_action(self) -> None:
        action = MatchAction(
            event_id="00000000-0000-0000-0000-000000000001",
            event_index=1,
            minute=10,
            second=1.2,
            team_id=1,
            team_name="Home",
            player_id=11,
            player_name="Creator",
            action_type="Pass",
            location_x=10.0,
            location_y=10.0,
            xt_start=0.01,
            xt_end=0.02,
            delta_xt=0.01,
        )
        response = MatchAnalyticsResponse(
            match_id=1,
            competition="League",
            season="2025/26",
            match_date="2026-01-01",
            teams=[
                MatchTeam(team_id=1, team_name="Home", side="home", score=1),
                MatchTeam(team_id=2, team_name="Away", side="away", score=0),
            ],
            model_id="singh-xt",
            model_version="v1",
            actions=[action],
            players=[],
            team_summaries=[],
        )
        self.assertEqual(response.actions[0].delta_xt, 0.01)


if __name__ == "__main__":
    unittest.main()
