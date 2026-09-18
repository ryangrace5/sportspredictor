import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module


class AdminTrackingTests(unittest.TestCase):
    @patch("app.grade_ungraded_predictions", return_value={"checked": 0, "graded": 0})
    @patch("app.record_prediction_snapshots", return_value={"eligible": 0, "inserted": 0})
    @patch("app.predict_game_totals", return_value=[])
    def test_admin_tracking_looks_one_day_ahead_for_football(
        self, mock_predict, _mock_record, _mock_grade
    ):
        old_token = os.environ.get("ADMIN_TOKEN")
        os.environ["ADMIN_TOKEN"] = "test-token"
        try:
            response = app_module.app.test_client().post(
                "/admin/tracking", headers={"X-Admin-Token": "test-token"}
            )
        finally:
            if old_token is None:
                os.environ.pop("ADMIN_TOKEN", None)
            else:
                os.environ["ADMIN_TOKEN"] = old_token

        self.assertEqual(response.status_code, 200)
        today = datetime.now(app_module.LOCAL_TIMEZONE).date()
        calls = [
            (call.args[0], call.kwargs["target_date"])
            for call in mock_predict.call_args_list
        ]
        self.assertEqual(
            calls,
            [
                ("NBA", today),
                ("MLB", today),
                ("NFL", today),
                ("NFL", today + timedelta(days=1)),
                ("NCAAF", today),
                ("NCAAF", today + timedelta(days=1)),
            ],
        )

    @patch("app.grade_ungraded_predictions", return_value={"checked": 0, "graded": 0})
    @patch("app.record_prediction_snapshots", return_value={"eligible": 0, "inserted": 0})
    @patch("app.select_best_bets", return_value=[])
    @patch("app.predict_game_totals", return_value=[])
    def test_homepage_looks_one_day_ahead_for_football(
        self, mock_predict, _mock_best_bets, _mock_record, _mock_grade
    ):
        response = app_module.app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        today = datetime.now(app_module.LOCAL_TIMEZONE).date()
        calls = [
            (call.args[0], call.kwargs["target_date"])
            for call in mock_predict.call_args_list
        ]
        self.assertEqual(
            calls,
            [
                ("NBA", today),
                ("MLB", today),
                ("NFL", today),
                ("NFL", today + timedelta(days=1)),
                ("NCAAF", today),
                ("NCAAF", today + timedelta(days=1)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
