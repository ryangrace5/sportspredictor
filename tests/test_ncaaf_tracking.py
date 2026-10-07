import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import requests

import tracking_service as tracking


def final_event(home="Georgia Bulldogs", away="Vanderbilt Commodores", home_score=38, away_score=14):
    return {
        "status": {"type": {"completed": True, "state": "post"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
            {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
        ]}],
    }


def scoreboard_response(events):
    response = Mock()
    response.json.return_value = {"events": events}
    return response


class OctoberClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = tracking.LOCAL_TIMEZONE.localize(cls(2026, 10, 7, 8, 0))
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class JanuaryClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = tracking.LOCAL_TIMEZONE.localize(cls(2027, 1, 10, 8, 0))
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class NcaafResultFetchTests(unittest.TestCase):
    @patch("tracking_service.requests.get")
    def test_blocked_scoreboard_uses_backup_and_keeps_shutout_scores(self, mock_get):
        blocked = Mock()
        blocked.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        final = final_event(home_score={"value": 0}, away_score={"displayValue": "7"})
        live = final_event()
        live["status"]["type"] = {"completed": False, "state": "in"}
        missing_score = final_event(away_score=None)
        mock_get.side_effect = [blocked, scoreboard_response([final, live, missing_score])]

        games = tracking._fetch_completed_games("NCAAF", "2026-10-03")

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["home_score"], 0.0)
        self.assertEqual(games[0]["actual_total"], 7.0)
        self.assertEqual(mock_get.call_args_list[1].args[0], tracking.NCAAF_SCOREBOARD_FALLBACK_URL)
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs["params"], {"dates": "20261003", "limit": 200, "groups": 80})
            self.assertNotIn("User-Agent", call.kwargs["headers"])

    @patch("tracking_service.requests.get")
    def test_empty_primary_feed_uses_backup(self, mock_get):
        mock_get.side_effect = [scoreboard_response([]), scoreboard_response([final_event()])]

        self.assertEqual(tracking._fetch_completed_games("NCAAF", "2026-10-03")[0]["actual_total"], 52.0)
        self.assertEqual(mock_get.call_count, 2)

    @patch("tracking_service.requests.get")
    def test_working_primary_does_not_request_backup(self, mock_get):
        mock_get.return_value = scoreboard_response([final_event()])

        self.assertEqual(len(tracking._fetch_completed_games("NCAAF", "2026-10-03")), 1)
        mock_get.assert_called_once()


class NcaafTrackingLifecycleTests(unittest.TestCase):
    @patch("tracking_service._read_tracking_rows", return_value=[])
    @patch("tracking_service._sheets_service")
    def test_ncaaf_snapshot_is_saved_once_and_keeps_original_pick(self, mock_service, mock_read):
        service = mock_service.return_value
        game_time = datetime.now(tracking.LOCAL_TIMEZONE) + timedelta(days=1)
        prediction = {
            "sport": "NCAAF", "game_time": game_time,
            "team1": "Texas Tech Red Raiders", "team2": "Houston Cougars",
            "away_team_raw": "Texas Tech Red Raiders", "home_team_raw": "Houston Cougars",
            "market_total": 51.5, "predicted_total": 55.0, "edge": 3.5,
            "pick": "OVER", "edge_tier": "Good Edge", "bookmaker_count": 4,
        }
        first = tracking.record_prediction_snapshots([prediction])
        append = service.spreadsheets().values().append
        saved_values = append.call_args.kwargs["body"]["values"][0]
        saved = dict(zip(tracking.HEADERS, saved_values))
        saved.update({"_sheet_row": 2, "_canonical_key": saved["canonical_key"]})
        mock_read.return_value = [saved]
        changed_prediction = {**prediction, "market_total": 54.5, "predicted_total": 50.0, "pick": "UNDER"}

        second = tracking.record_prediction_snapshots([changed_prediction])

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["line_updates"], 1)
        append.assert_called_once()
        self.assertEqual(saved["league"], "NCAAF")
        self.assertEqual(saved["market_total"], 51.5)
        self.assertEqual(saved["pick"], "OVER")

    @patch("tracking_service.datetime", OctoberClock)
    @patch("tracking_service._fetch_completed_games")
    @patch("tracking_service._read_tracking_rows")
    @patch("tracking_service._sheets_service")
    def test_grading_recovers_season_picks_and_updates_only_final_result_columns(
        self, mock_service, mock_rows, mock_games
    ):
        row = {
            "league": "NCAAF", "game_date": "2026-08-29", "_sheet_row": 2,
            "away_team_raw": "Texas Tech Red Raiders", "home_team_raw": "Houston Cougars",
            "market_total": "51.5", "bz_total": "55", "pick": "OVER", "status": "PENDING",
        }
        mock_rows.return_value = [
            row,
            {**row, "_sheet_row": 3, "game_date": "2026-09-19", "pick": "UNDER", "market_total": "56.5"},
            {**row, "_sheet_row": 4, "game_date": "2026-10-03", "market_total": "55"},
            {**row, "_sheet_row": 5, "league": "MLB"},
            {**row, "_sheet_row": 6, "game_date": "2025-09-01"},
            {**row, "_sheet_row": 7, "game_date": "2026-10-08"},
            {**row, "_sheet_row": 8, "status": "FINAL", "result": "WIN"},
        ]
        mock_games.return_value = [{
            "away_team": "Texas Tech Red Raiders", "home_team": "Houston Cougars",
            "away_score": 31.0, "home_score": 24.0, "actual_total": 55.0,
        }]

        summary = tracking.grade_ungraded_predictions(force=True)

        self.assertEqual(summary["checked"], 3)
        self.assertEqual(summary["graded_by_league"], {"NCAAF": 3})
        data = mock_service.return_value.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["data"]
        self.assertEqual([entry["range"] for entry in data], [
            "'Predictions'!Q2:V2", "'Predictions'!Q3:V3", "'Predictions'!Q4:V4",
        ])
        self.assertEqual([entry["values"][0][3] for entry in data], ["WIN", "WIN", "PUSH"])
        self.assertTrue(all(entry["values"][0][5] == "FINAL" for entry in data))
        self.assertEqual(row["market_total"], "51.5")
        self.assertEqual(row["bz_total"], "55")

    @patch("tracking_service.datetime", JanuaryClock)
    @patch("tracking_service._fetch_completed_games", return_value=[])
    @patch("tracking_service._read_tracking_rows")
    @patch("tracking_service._sheets_service")
    def test_bowl_season_can_grade_previous_calendar_year(self, _service, mock_rows, mock_games):
        mock_rows.return_value = [{
            "league": "NCAAF", "game_date": "2026-09-19", "status": "PENDING",
            "away_team_raw": "A", "home_team_raw": "B", "market_total": "45",
        }]

        summary = tracking.grade_ungraded_predictions(force=True)

        self.assertEqual(summary["checked"], 1)
        mock_games.assert_called_once_with("NCAAF", "2026-09-19")

    @patch("tracking_service._read_tracking_rows")
    @patch("tracking_service._sheets_service")
    def test_ncaaf_filter_reports_graded_record(self, _service, mock_rows):
        mock_rows.return_value = [{
            "league": "NCAAF", "game_date": "2026-10-03", "pick": "OVER",
            "status": "FINAL", "result": "WIN", "market_total": "51.5",
        }, {
            "league": "MLB", "game_date": "2026-10-03", "pick": "UNDER",
            "status": "FINAL", "result": "LOSS", "market_total": "8.5",
        }]
        with patch("tracking_service._filter_state", return_value={
            "window": "all", "league": "NCAAF", "pick": "ALL", "tier": "ALL",
        }):
            dashboard = tracking.get_performance_dashboard()

        self.assertEqual(dashboard["overall"]["record"], "1-0")
        self.assertEqual(dashboard["graded_bets"], 1)
        self.assertEqual(dashboard["by_league"][0]["label"], "NCAAF")


if __name__ == "__main__":
    unittest.main()
