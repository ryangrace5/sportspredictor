import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from backtesting_service import evaluate_pregame_snapshots
from odds_service import _find_market_for_prediction
from tracking_service import HEADERS, _find_completed_game, _grade_pick, _snapshot_id, record_prediction_snapshots


class ForecastAuditTests(unittest.TestCase):
    def test_no_line_then_first_quote_keeps_original_forecast(self):
        service = Mock()
        now = datetime.now(timezone.utc)
        pick = dict(sport="NBA", team1="A", team2="B", predicted_total=221,
                    game_time=now + timedelta(hours=2), pick="NO LINE")
        with patch("tracking_service._sheets_service", return_value=service), patch(
                "tracking_service._read_tracking_rows", return_value=[]):
            self.assertEqual(record_prediction_snapshots([pick])["inserted"], 1)
        api = service.spreadsheets().values()
        values = api.append.call_args.kwargs["body"]["values"][0]
        row = dict(zip(HEADERS, values), _sheet_row=2, _canonical_key=_snapshot_id(pick))
        self.assertEqual(row["first_bz_total"], 221)
        pick.update(predicted_total=225, market_total=226, pick="PASS", market_fetched_at=now)
        with patch("tracking_service._sheets_service", return_value=service), patch(
                "tracking_service._read_tracking_rows", return_value=[row]):
            record_prediction_snapshots([pick])
        new = dict(zip(HEADERS, api.batchUpdate.call_args.kwargs["body"]["data"][0]["values"][0]))
        new.update(status="FINAL", actual_total=227)
        league = evaluate_pregame_snapshots([new])["leagues"][1]
        self.assertEqual((league["forecast_mae"], league["bz_mae"], league["market_mae"]), (6, 2, 1))

    def test_doubleheader_separates_market_final_and_pick(self):
        first = datetime(2026, 8, 1, 17, tzinfo=timezone.utc)
        tips = [first, first + timedelta(hours=4)]
        picks = [dict(sport="MLB", team1="A", team2="B", mlb_game_pk=str(i),
                      game_time=t, doubleheader=True) for i, t in enumerate(tips, 1)]
        markets = [dict(home_team="B", away_team="A", commence_time=t) for t in tips]
        finals = [dict(mlb_game_pk=str(i), home_team="B", away_team="A", actual_total=n)
                  for i, n in [(1, 6), (2, 12)]]
        for i, pick in enumerate(picks):
            self.assertIs(_find_market_for_prediction(pick, reversed(markets)), markets[i])
            self.assertIsNone(_find_market_for_prediction(pick, [markets[1-i]]))
            game = _find_completed_game(dict(league="MLB", canonical_key=_snapshot_id(pick)), reversed(finals))
            self.assertEqual(_grade_pick("OVER", 9.5, game["actual_total"]), ["LOSS", "WIN"][i])
