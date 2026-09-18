import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pandas as pd

import quality_app


class QualityAppTests(unittest.TestCase):
    @patch("quality_app._add_confidence")
    @patch("quality_app._ORIGINAL_PREDICT")
    def test_prediction_wrapper_forwards_tracking_date(
        self, mock_predict, mock_confidence
    ):
        predictions = [{"sport": "NFL"}]
        target_date = date(2026, 9, 14)
        mock_predict.return_value = predictions
        mock_confidence.return_value = predictions

        result = quality_app.predict_game_totals_with_quality(
            "NFL", target_date=target_date
        )

        mock_predict.assert_called_once_with("NFL", target_date=target_date)
        mock_confidence.assert_called_once_with(predictions, "NFL")
        self.assertEqual(result, predictions)

    @patch("quality_app.legacy.fetch_data_from_sheets")
    @patch("quality_app.legacy.get_todays_games")
    def test_data_health_detects_missing_stats_for_tomorrows_football(
        self, mock_schedule, mock_stats
    ):
        today = datetime.now(quality_app.legacy.LOCAL_TIMEZONE).date()
        tomorrow = today + timedelta(days=1)
        mock_schedule.side_effect = [[], [
            {"strHomeTeam": "Houston Texans", "strAwayTeam": "Missing Opponent"}
        ]]
        mock_stats.return_value = pd.DataFrame(
            {"G": [2], "PF": [40], "PA": [30]}, index=["Houston Texans"]
        )

        result = quality_app.sport_data_health("NFL")

        self.assertEqual(result["schedule_games"], 1)
        self.assertEqual(result["missing_teams"], ["Missing Opponent"])
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["date_start"], today.isoformat())
        self.assertEqual(result["date_end"], tomorrow.isoformat())


if __name__ == "__main__":
    unittest.main()
