import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from backtesting_service import evaluate_pregame_snapshots


class PregameBacktestTests(unittest.TestCase):
    def row(self, **overrides):
        defaults = {
            "league": "NFL",
            "status": "FINAL",
            "game_date": "2026-11-01",
            "game_time_ct": "2026-11-01T18:00:00+00:00",
            "snapshot_at_ct": "2026-11-01T16:00:00+00:00",
            "quote_fetched_at": "2026-11-01T15:30:00+00:00",
            "bz_total": "48.0",
            "market_total": "46.0",
            "actual_total": "47.0",
            "model_version": "baseline_pfpa_v1",
        }
        defaults.update(overrides)
        return defaults

    def test_compares_bz_to_market_on_exactly_same_games(self):
        rows = [
            self.row(bz_total="50", market_total="48", actual_total="49"),
            self.row(bz_total="40", market_total="44", actual_total="43"),
            self.row(bz_total="35", market_total="33", actual_total="35"),
        ]
        result = evaluate_pregame_snapshots(rows)
        nfl = result["leagues"][0]
        self.assertEqual(result["eligible"], 3)
        self.assertEqual(nfl["bz_mae"], 1.33)
        self.assertEqual(nfl["market_mae"], 1.33)
        self.assertEqual(nfl["bz_rmse"], 1.83)
        self.assertEqual(nfl["market_rmse"], 1.41)
        self.assertEqual(nfl["bz_bias"], -0.67)
        self.assertEqual(nfl["market_bias"], -0.67)
        self.assertEqual(nfl["mae_advantage"], 0)
        self.assertEqual((nfl["bz_more_accurate"], nfl["market_more_accurate"], nfl["tied"]), (1, 1, 1))

    def test_bz_mae_advantage_is_positive_only_when_more_accurate(self):
        good = self.row(bz_total="47", market_total="44", actual_total="48")
        bad = self.row(bz_total="43", market_total="48", actual_total="48")
        self.assertEqual(evaluate_pregame_snapshots([good])["leagues"][0]["mae_advantage"], 3.0)
        self.assertEqual(evaluate_pregame_snapshots([bad])["leagues"][0]["mae_advantage"], -5.0)

    def test_filters_leagues_and_reports_no_data_as_null_metrics(self):
        result = evaluate_pregame_snapshots([
            self.row(league="NBA", model_version="nba_regular_season_blend_v1"),
            self.row(league="NCAAF"),
        ])
        self.assertEqual(result["tracked"], 1)
        self.assertEqual(result["leagues"][0]["count"], 0)
        self.assertIsNone(result["leagues"][0]["bz_mae"])
        self.assertEqual(result["leagues"][1]["count"], 1)

    def test_requires_a_final_score_and_valid_finite_totals(self):
        rows = [
            self.row(status="PENDING"),
            self.row(actual_total=""),
            self.row(bz_total="NaN"),
            self.row(market_total="inf"),
            self.row(actual_total="-1"),
            self.row(bz_total="0"),
        ]
        result = evaluate_pregame_snapshots(rows)
        self.assertEqual(result["eligible"], 0)
        self.assertEqual(result["excluded"], len(rows))
        self.assertEqual(result["exclusion_reasons"]["not_final"], 1)
        self.assertEqual(result["exclusion_reasons"]["missing_or_invalid_totals"], 5)

    def test_rejects_missing_naive_and_unverifiable_timestamps(self):
        rows = [
            self.row(quote_fetched_at=""),
            self.row(snapshot_at_ct="2026-11-01T16:00:00"),
            self.row(game_time_ct="invalid"),
            self.row(quote_fetched_at="2026-11-01T16:10:00+00:00"),
            self.row(snapshot_at_ct="2026-11-01T18:00:00+00:00"),
            self.row(quote_fetched_at="2026-11-01T12:00:00+00:00"),
        ]
        result = evaluate_pregame_snapshots(rows)
        self.assertEqual(result["eligible"], 0)
        self.assertEqual(result["exclusion_reasons"]["missing_verified_timestamps"], 3)
        self.assertEqual(result["exclusion_reasons"]["not_verified_pregame"], 2)
        self.assertEqual(result["exclusion_reasons"]["stale_market_quote"], 1)

    def test_timezone_offsets_are_normalized_before_pregame_checks(self):
        # 10:00 CST corresponds to 16:00 UTC, prior to 18:00 UTC kickoff.
        row = self.row(
            snapshot_at_ct="2026-11-01T10:00:00-06:00",
            quote_fetched_at="2026-11-01T09:30:00-06:00",
        )
        self.assertEqual(evaluate_pregame_snapshots([row])["eligible"], 1)

    def test_keeps_model_versions_separate_and_avoids_missing_versions(self):
        result = evaluate_pregame_snapshots([
            self.row(model_version="baseline_pfpa_v1"),
            self.row(model_version="experimental_v2", bz_total="45"),
            self.row(model_version=""),
        ])
        league = result["leagues"][0]
        self.assertEqual(league["count"], 2)
        self.assertEqual([v["version"] for v in league["versions"]], ["baseline_pfpa_v1", "experimental_v2"])
        self.assertEqual(result["exclusion_reasons"]["missing_model_version"], 1)

    def test_no_mutation_and_repeated_calls_are_deterministic(self):
        rows = [self.row()]
        original = deepcopy(rows)
        result = evaluate_pregame_snapshots(rows)
        self.assertEqual(rows, original)
        self.assertEqual(result, evaluate_pregame_snapshots(rows))

    def test_sample_quality_threshold_is_descriptive_not_approval(self):
        rows = [self.row()] * 29
        self.assertFalse(evaluate_pregame_snapshots(rows)["leagues"][0]["sample_sufficient"])
        self.assertTrue(evaluate_pregame_snapshots(rows + [self.row()])["leagues"][0]["sample_sufficient"])


if __name__ == "__main__":
    unittest.main()
