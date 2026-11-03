import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from tracking_service import (
    _aggregate,
    _ensure_tracking_sheet,
    _quoted_paper_summary,
    HEADERS,
    record_prediction_snapshots,
    _canonical_game_key,
    _clv_value,
    _fetch_completed_games,
    _fetch_completed_espn_games,
    _fetch_completed_nfl_sportsdb_games,
    _freeze_prediction_from_row,
    _grade_pick,
    _mark_missed_pregame,
    _snapshot_id,
    _teams_match,
)


class TrackingServiceTests(unittest.TestCase):
    def test_grade_over_under_and_push(self):
        self.assertEqual(_grade_pick("OVER", 8.5, 10.0), "WIN")
        self.assertEqual(_grade_pick("OVER", 8.5, 7.0), "LOSS")
        self.assertEqual(_grade_pick("UNDER", 8.5, 7.0), "WIN")
        self.assertEqual(_grade_pick("UNDER", 8.5, 10.0), "LOSS")
        self.assertEqual(_grade_pick("UNDER", 8.0, 8.0), "PUSH")
        self.assertEqual(_grade_pick("PASS", 8.0, 11.0), "PASS")

    def test_team_matching_handles_common_provider_variants(self):
        self.assertTrue(_teams_match("Oakland Athletics", "Athletics"))
        self.assertTrue(_teams_match("Texas Rangers", "Texas Rangers"))
        self.assertFalse(_teams_match("Texas Rangers", "Houston Astros"))

    def test_canonical_key_does_not_depend_on_provider_event_id(self):
        prediction_a = {
            "sport": "MLB",
            "team1": "Astros",
            "team2": "Rangers",
            "away_team_raw": "Houston Astros",
            "home_team_raw": "Texas Rangers",
            "odds_event_id": "provider-id-1",
        }
        prediction_b = {**prediction_a, "odds_event_id": "provider-id-2"}
        self.assertEqual(_snapshot_id(prediction_a), _snapshot_id(prediction_b))
        self.assertEqual(
            _canonical_game_key("MLB", "2026-08-24", "Houston Astros", "Texas Rangers"),
            "MLB:2026-08-24:houston astros:texas rangers",
        )

    def test_line_value_is_positive_when_market_moves_with_pick(self):
        self.assertEqual(_clv_value("OVER", 8.0, 9.0), 1.0)
        self.assertEqual(_clv_value("UNDER", 9.0, 8.0), 1.0)
        self.assertEqual(_clv_value("OVER", 9.0, 8.5), -0.5)
        self.assertEqual(_clv_value("UNDER", 8.0, 8.5), -0.5)
        self.assertIsNone(_clv_value("PASS", 8.0, 9.0))

    @patch("tracking_service.requests.get")
    def test_completed_mlb_statsapi_parsing(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "dates": [
                {
                    "games": [
                        {
                            "status": {
                                "abstractGameState": "Final",
                                "detailedState": "Final",
                                "codedGameState": "F",
                            },
                            "teams": {
                                "away": {
                                    "score": 4,
                                    "team": {"name": "Texas Rangers"},
                                },
                                "home": {
                                    "score": 6,
                                    "team": {"name": "Houston Astros"},
                                },
                            },
                        }
                    ]
                }
            ]
        }
        mock_get.return_value = response

        games = _fetch_completed_games("MLB", "2026-08-24")
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["away_score"], 4.0)
        self.assertEqual(games[0]["home_score"], 6.0)
        self.assertEqual(games[0]["actual_total"], 10.0)

    @patch("tracking_service.requests.get")
    def test_ncaaf_scoreboard_uses_supported_result_limit(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"events": []}
        mock_get.return_value = response

        _fetch_completed_espn_games(" ncaaf ", "2026-09-12")

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["limit"], 200)
        self.assertEqual(params["groups"], 80)

    def test_completed_game_matches_neutral_site_home_away_flip(self):
        from tracking_service import _find_completed_game

        row = {
            "away_team_raw": "Ohio State Buckeyes",
            "home_team_raw": "Texas Longhorns",
        }
        games = [{
            "away_team": "Texas Longhorns",
            "home_team": "Ohio State Buckeyes",
            "away_score": 24.0,
            "home_score": 23.0,
            "actual_total": 47.0,
        }]

        game = _find_completed_game(row, games)
        self.assertEqual(game["away_score"], 23.0)
        self.assertEqual(game["home_score"], 24.0)
        self.assertEqual(game["actual_total"], 47.0)

    @patch("tracking_service.requests.get")
    def test_sportsdb_nfl_fallback_parses_final_local_date(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "events": [
                {
                    "dateEvent": "2026-09-13",
                    "strTime": "17:00:00",
                    "strStatus": "FT",
                    "strAwayTeam": "Tampa Bay Buccaneers",
                    "strHomeTeam": "Cincinnati Bengals",
                    "intAwayScore": "27",
                    "intHomeScore": "33",
                },
                {
                    "dateEvent": "2026-09-14",
                    "strTime": "00:20:00",
                    "strStatus": "NS",
                    "strAwayTeam": "Dallas Cowboys",
                    "strHomeTeam": "New York Giants",
                    "intAwayScore": None,
                    "intHomeScore": None,
                },
            ]
        }
        mock_get.return_value = response

        games = _fetch_completed_nfl_sportsdb_games("2026-09-13")

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["actual_total"], 60.0)
        self.assertEqual(mock_get.call_args.kwargs["params"]["s"], 2026)

    @patch("tracking_service._fetch_completed_nfl_sportsdb_games")
    @patch("tracking_service._fetch_completed_espn_games", return_value=[])
    def test_nfl_grading_falls_back_when_espn_returns_no_games(
        self, _mock_espn, mock_sportsdb
    ):
        fallback = [{"away_team": "A", "home_team": "B", "actual_total": 42.0}]
        mock_sportsdb.return_value = fallback

        self.assertEqual(_fetch_completed_games("NFL", "2026-09-13"), fallback)
        mock_sportsdb.assert_called_once_with("2026-09-13")

    def test_started_game_is_frozen_to_original_snapshot(self):
        prediction = {
            "predicted_total": 12.0,
            "market_total": 13.5,
            "edge": -1.5,
            "abs_edge": 1.5,
            "pick": "UNDER",
            "edge_tier": "Strong Edge",
            "best_book": "Live Book",
            "best_line": 14.5,
            "best_price": -110,
            "best_abs_edge": 2.5,
        }
        tracked = {
            "bz_total": "9.2",
            "market_total": "8.5",
            "edge": "0.7",
            "pick": "OVER",
            "edge_tier": "Lean",
            "bookmaker_count": "6",
            "model_version": "mlb_form_pitching_v2",
        }
        result = _freeze_prediction_from_row(prediction, tracked)
        self.assertEqual(result["predicted_total"], 9.2)
        self.assertEqual(result["market_total"], 8.5)
        self.assertEqual(result["pick"], "OVER")
        self.assertEqual(result["edge"], 0.7)
        self.assertIsNone(result["abs_edge"])
        self.assertIsNone(result["best_line"])
        self.assertTrue(result["pick_locked"])

    def test_missed_pregame_never_displays_live_pick(self):
        prediction = {
            "market_total": 11.5,
            "edge": 2.0,
            "pick": "OVER",
            "best_line": 11.0,
        }
        result = _mark_missed_pregame(prediction)
        self.assertEqual(result["pick"], "NO BET")
        self.assertIsNone(result["market_total"])
        self.assertIsNone(result["best_line"])
        self.assertEqual(result["tracking_status"], "MISSED_PREGAME")

    def test_aggregate_uses_standard_minus_110_flat_units(self):
        rows = [
            {"result": "WIN"},
            {"result": "WIN"},
            {"result": "LOSS"},
            {"result": "PUSH"},
        ]
        summary = _aggregate(rows)
        self.assertEqual(summary["wins"], 2)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["pushes"], 1)
        self.assertEqual(summary["hit_rate"], 66.7)
        self.assertEqual(summary["flat_units"], 0.82)


    def test_quoted_paper_results_use_actual_book_prices(self):
        base = {
            "pick": "OVER", "status": "FINAL", "quote_book": "Example Book",
            "snapshot_at_ct": "2026-11-01T14:00:00+00:00",
            "quote_fetched_at": "2026-11-01T13:30:00+00:00",
            "game_time_ct": "2026-11-01T18:00:00+00:00",
        }
        rows = [
            {**base, "quote_line": "44.5", "quote_price": "-110", "actual_total": "45"},
            {**base, "pick": "UNDER", "quote_line": "48.5", "quote_price": "+120", "actual_total": "47"},
            {**base, "quote_line": "44.5", "quote_price": "-105", "actual_total": "43"},
            {**base, "quote_line": "44.0", "quote_price": "-110", "actual_total": "44"},
        ]
        summary = _quoted_paper_summary(rows)
        self.assertEqual(summary["count"], 4)
        self.assertEqual((summary["wins"], summary["losses"], summary["pushes"]), (2, 1, 1))
        self.assertEqual(summary["flat_units"], 1.11)
        self.assertEqual(summary["roi_pct"], 27.7)

    def test_quoted_paper_grades_the_quoted_line_not_consensus(self):
        row = {
            "pick": "OVER", "result": "WIN", "market_total": "44.5",
            "quote_book": "Example Book", "quote_line": "46.5", "quote_price": "-110",
            "actual_total": "45", "status": "FINAL",
            "snapshot_at_ct": "2026-11-01T14:00:00+00:00",
            "quote_fetched_at": "2026-11-01T13:30:00+00:00",
            "game_time_ct": "2026-11-01T18:00:00+00:00",
        }
        result = _quoted_paper_summary([row])
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["flat_units"], -1.0)

    def test_stale_or_unpriced_quotes_are_not_counted(self):
        base = {
            "pick": "OVER", "status": "FINAL", "quote_book": "Example",
            "quote_line": "44.5", "quote_price": "-110", "actual_total": "48",
            "snapshot_at_ct": "2026-11-01T14:00:00+00:00",
            "quote_fetched_at": "2026-11-01T11:00:00+00:00",
            "game_time_ct": "2026-11-01T18:00:00+00:00",
        }
        cases = [
            base,  # price fetched three hours before the snapshot
            {**base, "quote_fetched_at": "", "result": "WIN"},
            {**base, "quote_fetched_at": "2026-11-01T13:30:00+00:00", "quote_price": "-80"},
            {**base, "quote_fetched_at": "2026-11-01T13:30:00+00:00", "quote_book": ""},
            {**base, "quote_fetched_at": "2026-11-01T13:30:00+00:00", "game_time_ct": "2026-11-01T13:00:00+00:00"},
        ]
        result = _quoted_paper_summary(cases)
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["roi_pct"])

    def test_tracking_snapshots_append_quote_without_changing_legacy_columns(self):
        service = Mock()
        now = datetime.now(timezone.utc)
        prediction = {
            "sport": "NFL", "team1": "Away", "team2": "Home",
            "game_time": now + timedelta(days=1),
            "market_total": 44.5, "predicted_total": 47.5, "edge": 3.0,
            "pick": "OVER", "best_book": "Example Book",
            "best_line": 44.0, "best_price": -105,
            "market_fetched_at": now - timedelta(minutes=15),
        }
        with patch("tracking_service._sheets_service", return_value=service), patch(
            "tracking_service._read_tracking_rows", side_effect=[[], []]
        ):
            result = record_prediction_snapshots([prediction])

        self.assertEqual(result["inserted"], 1)
        request = service.spreadsheets().values().append.call_args.kwargs
        self.assertTrue(request["range"].endswith("!A:AG"))
        row = request["body"]["values"][0]
        self.assertEqual(len(row), len(HEADERS))
        self.assertEqual(row[8], 44.5)  # unchanged consensus column
        self.assertEqual(row[-4:], [
            "Example Book", 44.0, -105,
            (now - timedelta(minutes=15)).isoformat(),
        ])

    def test_tracking_sheet_expands_existing_29_column_grid(self):
        service = Mock()
        service.spreadsheets().get().execute.return_value = {
            "sheets": [{
                "properties": {
                    "sheetId": 7, "title": "Predictions",
                    "gridProperties": {"columnCount": 29},
                }
            }]
        }
        _ensure_tracking_sheet(service)
        updates = service.spreadsheets().batchUpdate.call_args.kwargs
        request = updates["body"]["requests"][0]["appendDimension"]
        self.assertEqual(request, {"sheetId": 7, "dimension": "COLUMNS", "length": 4})
        self.assertTrue(service.spreadsheets().values().update.call_args.kwargs["range"].endswith("!A1:AG1"))



if __name__ == "__main__":
    unittest.main()
