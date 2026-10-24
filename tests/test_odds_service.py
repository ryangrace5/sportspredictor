import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

from odds_service import (
    _extract_consensus_total,
    _select_best_offer,
    enrich_predictions_with_odds,
    _find_market_for_prediction,
    fetch_totals_market,
)


class OddsServiceTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "event-1",
            "home_team": "Houston Astros",
            "away_team": "Texas Rangers",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 8.5},
                                {"name": "Under", "price": -110, "point": 8.5},
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -105, "point": 8.0},
                                {"name": "Under", "price": -115, "point": 8.0},
                            ],
                        }
                    ],
                },
                {
                    "key": "betmgm",
                    "title": "BetMGM",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 100, "point": 8.5},
                                {"name": "Under", "price": -120, "point": 9.0},
                            ],
                        }
                    ],
                },
            ],
        }

    def test_extracts_consensus_and_book_prices(self):
        parsed = _extract_consensus_total(self.event)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["market_total"], 8.5)
        self.assertEqual(parsed["bookmaker_count"], 3)
        self.assertEqual(len(parsed["offers"]), 6)

        dk = next(row for row in parsed["bookmaker_lines"] if row["bookmaker"] == "DraftKings")
        self.assertEqual(dk["over_price"], -110)
        self.assertEqual(dk["under_price"], -110)

    def test_best_over_prefers_lower_total_before_price(self):
        parsed = _extract_consensus_total(self.event)
        best = _select_best_offer(parsed["offers"], "OVER")
        self.assertEqual(best["bookmaker"], "FanDuel")
        self.assertEqual(best["total"], 8.0)
        self.assertEqual(best["price"], -105)

    def test_best_under_prefers_higher_total(self):
        parsed = _extract_consensus_total(self.event)
        best = _select_best_offer(parsed["offers"], "UNDER")
        self.assertEqual(best["bookmaker"], "BetMGM")
        self.assertEqual(best["total"], 9.0)
        self.assertEqual(best["price"], -120)

    def test_same_line_uses_better_american_price(self):
        offers = [
            {"bookmaker": "Book A", "direction": "OVER", "total": 8.5, "price": -110},
            {"bookmaker": "Book B", "direction": "OVER", "total": 8.5, "price": -105},
            {"bookmaker": "Book C", "direction": "OVER", "total": 8.5, "price": 100},
        ]
        best = _select_best_offer(offers, "OVER")
        self.assertEqual(best["bookmaker"], "Book C")
        self.assertEqual(best["price"], 100)

    def test_repeated_matchup_from_wrong_date_cannot_supply_market(self):
        t = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
        prediction = {
            "home_team_raw": "Houston Astros",
            "away_team_raw": "Texas Rangers",
            "game_time": t,
        }
        wrong_date = [{
            "home_team": "Houston Astros",
            "away_team": "Texas Rangers",
            "commence_time": t + timedelta(days=1),
        }]
        self.assertIsNone(_find_market_for_prediction(prediction, wrong_date))

    def test_correct_game_time_can_be_verified(self):
        t = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
        market = {
            "event_id": "test",
            "home_team": "Houston Astros",
            "away_team": "Texas Rangers",
            "commence_time": t + timedelta(minutes=15),
            **_extract_consensus_total(self.event),
        }
        prediction = {
            "sport": "MLB",
            "team1": "Texas Rangers",
            "team2": "Houston Astros",
            "home_team_raw": "Houston Astros",
            "away_team_raw": "Texas Rangers",
            "game_time": t,
            "predicted_total": 10.0,
        }
        with patch("odds_service.fetch_totals_market", return_value=[market]):
            with patch("odds_service.enrich_mlb_predictions", side_effect=lambda x: x):
                enriched = enrich_predictions_with_odds([prediction], "MLB")
        self.assertTrue(enriched[0]["market_time_verified"])

    @patch("odds_service.enrich_mlb_predictions", side_effect=lambda predictions: predictions)
    @patch("odds_service.fetch_totals_market")
    def test_enrichment_exposes_best_book_and_best_edge(self, mock_fetch, _mock_mlb):
        parsed = _extract_consensus_total(self.event)
        mock_fetch.return_value = [
            {
                "event_id": "event-1",
                "home_team": "Houston Astros",
                "away_team": "Texas Rangers",
                "commence_time": None,
                **parsed,
            }
        ]
        predictions = [
            {
                "sport": "MLB",
                "team1": "Rangers",
                "team2": "Astros",
                "away_team_raw": "Texas Rangers",
                "home_team_raw": "Houston Astros",
                "predicted_total": 10.0,
                "game_time": None,
            }
        ]

        enriched = enrich_predictions_with_odds(predictions, "MLB")[0]
        self.assertEqual(enriched["pick"], "OVER")
        self.assertEqual(enriched["market_total"], 8.5)
        self.assertEqual(enriched["best_book"], "FanDuel")
        self.assertEqual(enriched["best_line"], 8.0)
        self.assertEqual(enriched["best_price"], -105)
        self.assertEqual(enriched["edge"], 1.5)
        self.assertEqual(enriched["best_edge"], 2.0)
        self.assertEqual(enriched["line_improvement"], 0.5)

    @patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-not-a-real-secret"})
    @patch("odds_service.requests.get")
    def test_cache_prevents_repeated_api_calls_on_page_loads(self, mock_get):
        response = Mock()
        response.json.return_value = [self.event]
        response.headers = {"x-requests-remaining": "490"}
        mock_get.return_value = response

        with patch("odds_service._cache", {}), patch("odds_service._failed_until", {}), patch("odds_service._out_of_credits_until", None):
            first = fetch_totals_market("NCAAF")
            for _ in range(12):
                self.assertEqual(fetch_totals_market("NCAAF"), first)

        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs["params"]["markets"], "totals")
        self.assertTrue(mock_get.call_args.args[0].endswith("/americanfootball_ncaaf/odds"))
        self.assertIsInstance(first[0]["market_fetched_at"], datetime)

    @patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-not-a-real-secret"})
    @patch("odds_service.requests.get")
    def test_zero_remaining_stops_other_leagues_until_month_reset(self, mock_get):
        response = Mock()
        response.json.return_value = [self.event]
        response.headers = {"x-requests-remaining": "0"}
        mock_get.return_value = response

        with patch("odds_service._cache", {}), patch("odds_service._failed_until", {}), patch("odds_service._out_of_credits_until", None):
            self.assertEqual(len(fetch_totals_market("NCAAF")), 1)
            self.assertEqual(fetch_totals_market("NBA"), [])
            self.assertEqual(len(fetch_totals_market("NCAAF")), 1)

        mock_get.assert_called_once()

    @patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-not-a-real-secret"})
    @patch("odds_service.requests.get")
    def test_quota_error_pauses_future_calls_without_leaking_key(self, mock_get):
        failed_response = Mock(status_code=401)
        failed_response.json.return_value = {"error_code": "OUT_OF_USAGE_CREDITS"}
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(response=failed_response)

        with patch("odds_service._cache", {}), patch("odds_service._failed_until", {}), patch("odds_service._out_of_credits_until", None):
            with patch("odds_service.logging.warning") as mock_log:
                self.assertEqual(fetch_totals_market("NCAAF"), [])
                self.assertEqual(fetch_totals_market("NFL"), [])

        mock_get.assert_called_once()
        message = " ".join(str(x) for call in mock_log.call_args_list for x in call.args)
        self.assertNotIn("test-not-a-real-secret", message)

    @patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-not-a-real-secret"})
    @patch("odds_service.requests.get", side_effect=requests.Timeout("secret-in-request-url"))
    def test_transient_failure_has_backoff_and_does_not_log_request_url(self, mock_get):
        with patch("odds_service._cache", {}), patch("odds_service._failed_until", {}), patch("odds_service._out_of_credits_until", None):
            with patch("odds_service.logging.warning") as mock_log:
                self.assertEqual(fetch_totals_market("NCAAF"), [])
                self.assertEqual(fetch_totals_market("NCAAF"), [])

        mock_get.assert_called_once()
        message = " ".join(str(x) for call in mock_log.call_args_list for x in call.args)
        self.assertNotIn("secret-in-request-url", message)

    @patch("odds_service.fetch_totals_market", return_value=[])
    def test_missing_odds_preserves_projected_total_without_inventing_pick(self, mock_fetch):
        prediction = {
            "sport": "NCAAF",
            "team1": "Texas Tech Red Raiders",
            "team2": "Houston Cougars",
            "predicted_total": 56.5,
        }
        result = enrich_predictions_with_odds([prediction], "NCAAF")[0]
        self.assertEqual(result["predicted_total"], 56.5)
        self.assertEqual(result["pick"], "NO LINE")
        self.assertIsNone(result["edge"])
        self.assertIsNone(result["market_total"])
        mock_fetch.assert_called_once_with("NCAAF")


if __name__ == "__main__":
    unittest.main()
