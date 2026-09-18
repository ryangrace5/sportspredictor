import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

import app as app_module


def scoreboard_event(home, away, kickoff):
    return {
        "date": kickoff,
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ],
        }],
    }


def json_response(data, status=200):
    response = Mock(status_code=status)
    response.json.return_value = data
    return response


class NcaafScheduleTests(unittest.TestCase):
    @patch("app.requests.get")
    def test_schedule_keeps_evening_games_on_their_chicago_date(self, mock_get):
        mock_get.return_value = json_response({"events": [
            scoreboard_event("Texas Tech Red Raiders", "Houston Cougars", "2026-09-19T00:00Z"),
            scoreboard_event("Oregon Ducks", "Portland State Vikings", "2026-09-19T02:30Z"),
            scoreboard_event("UCLA Bruins", "Purdue Boilermakers", "2026-09-20T03:00Z"),
            {"competitions": [{"date": "invalid", "competitors": []}]},
        ]})

        games = app_module.get_todays_games("NCAAF", target_date=date(2026, 9, 18))

        self.assertEqual([g["strHomeTeam"] for g in games], ["Texas Tech Red Raiders", "Oregon Ducks"])
        self.assertEqual(games[0]["dateEvent"], "2026-09-19")
        self.assertEqual(games[0]["strTime"], "00:00:00")

    @patch("app.requests.get")
    def test_core_fallback_resolves_references_and_all_pages(self, mock_get):
        base = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
        listing = f"{base}/events?dates=20260918"
        embedded = scoreboard_event("Oregon Ducks", "Portland State Vikings", "2026-09-19T02:30Z")
        responses = {
            listing: {"pageCount": 2, "items": [{"$ref": f"{base}/events/1".replace("https:", "http:")}]},
            listing + "&page=2": {"items": [embedded, {"date": "invalid"}]},
            f"{base}/events/1": {
                "date": "2026-09-19T00:00Z",
                "competitions": [{"$ref": f"{base}/events/1/competitions/1"}],
            },
            f"{base}/events/1/competitions/1": {
                "date": "2026-09-19T00:00Z",
                "competitors": [
                    {"homeAway": "home", "team": {"$ref": f"{base}/teams/1"}},
                    {"homeAway": "away", "team": {"$ref": f"{base}/teams/2"}},
                ],
            },
            f"{base}/teams/1": {"displayName": "Texas Tech Red Raiders"},
            f"{base}/teams/2": {"displayName": "Houston Cougars"},
        }

        def get(url, **kwargs):
            if "scoreboard" in url:
                return json_response({}, status=403)
            return json_response(responses[url])

        mock_get.side_effect = get
        games = app_module.get_todays_games("NCAAF", target_date=date(2026, 9, 18))

        self.assertEqual(len(games), 2)
        self.assertEqual({g["strHomeTeam"] for g in games}, {"Texas Tech Red Raiders", "Oregon Ducks"})
        self.assertTrue(all(call.args[0].startswith("https://") for call in mock_get.call_args_list))

    @patch("app.save_mapping")
    @patch("app.ncaaf_map", {})
    @patch("app.fetch_data_from_sheets")
    @patch("odds_service.fetch_totals_market")
    @patch("app.requests.get")
    def test_ncaaf_schedule_stats_and_market_produce_a_dated_play(
        self, mock_get, mock_market, mock_stats, _mock_save
    ):
        mock_get.return_value = json_response({"events": [
            scoreboard_event("Texas Tech Red Raiders", "Houston Cougars", "2026-09-19T00:00Z")
        ]})
        mock_stats.return_value = pd.DataFrame(
            {"G": [2, 2], "PF": [80, 60], "PA": [40, 40]},
            index=["Texas Tech Red Raiders", "Houston Cougars"],
        )
        mock_market.return_value = [{
            "event_id": "test-event",
            "home_team": "Texas Tech Red Raiders",
            "away_team": "Houston Cougars",
            "commence_time": datetime(2026, 9, 19, tzinfo=timezone.utc),
            "market_total": 51.5,
            "bookmaker_count": 1,
            "offers": [{"bookmaker": "Test Book", "direction": "OVER", "total": 51.5, "price": -110}],
        }]

        predictions = app_module.predict_game_totals("NCAAF", target_date=date(2026, 9, 18))

        self.assertEqual(len(predictions), 1)
        prediction = predictions[0]
        self.assertEqual(prediction["pick"], "OVER")
        self.assertEqual(prediction["predicted_total"], 55.0)
        self.assertEqual(prediction["market_total"], 51.5)
        self.assertEqual(prediction["display_date"], "Fri, Sep 18")
        self.assertEqual(prediction["display_time"], "07:00 PM")
        self.assertEqual(prediction["best_book"], "Test Book")


if __name__ == "__main__":
    unittest.main()
