import unittest
from unittest.mock import patch

import app
import tracking_service


class BacktestPerformanceIntegrationTests(unittest.TestCase):
    def test_archive_comparison_ignores_pick_and_tier_filters(self):
        saved_row = {
            "league": "NFL",
            "pick": "OVER",
            "edge_tier": "Strong Edge",
            "status": "FINAL",
            "result": "WIN",
            "game_date": "2026-11-01",
            "game_time_ct": "2026-11-01T18:00:00+00:00",
            "snapshot_at_ct": "2026-11-01T16:00:00+00:00",
            "quote_fetched_at": "2026-11-01T15:30:00+00:00",
            "bz_total": "48",
            "market_total": "46",
            "actual_total": "47",
            "model_version": "baseline_pfpa_v1",
            "_sheet_row": 2,
        }
        with app.app.test_request_context("/performance?pick=UNDER&tier=Lean"):
            with patch("tracking_service._sheets_service"), patch(
                "tracking_service._read_tracking_rows", side_effect=[
                    [saved_row], [saved_row]
                ]
            ):
                dashboard = tracking_service.get_performance_dashboard()

            self.assertEqual(dashboard["graded_bets"], 0)
            self.assertEqual(dashboard["backtest"]["eligible"], 1)
            self.assertEqual(dashboard["backtest"]["leagues"][0]["count"], 1)
            rendered = app.app.jinja_env.get_template("performance.html").render(
                dashboard=dashboard,
                logo_url="/static/test.png",
            )
            self.assertIn("Pregame prediction accuracy", rendered)
            self.assertIn("Market MAE", rendered)
            self.assertIn("baseline_pfpa_v1", rendered)

    def test_dashboard_returns_empty_report_when_sheets_unavailable(self):
        with app.app.test_request_context("/performance"):
            with patch("tracking_service._sheets_service", side_effect=RuntimeError("offline")):
                dashboard = tracking_service.get_performance_dashboard()
            self.assertEqual(dashboard["backtest"]["eligible"], 0)
            self.assertEqual(dashboard["backtest"]["tracked"], 0)
            self.assertTrue(dashboard["error"])


if __name__ == "__main__":
    unittest.main()
