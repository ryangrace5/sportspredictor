import unittest

from quality_metrics import confidence_for_prediction


class QualityMetricsTests(unittest.TestCase):
    def test_pass_has_no_confidence_score(self):
        result = confidence_for_prediction(
            {"sport": "NFL", "pick": "PASS", "edge": 0.5, "bookmaker_count": 8},
            min_team_games=6,
        )
        self.assertIsNone(result["confidence_score"])
        self.assertEqual(result["confidence_label"], "No play")

    def test_stronger_edge_and_sample_raise_confidence(self):
        thin = confidence_for_prediction(
            {"sport": "NFL", "pick": "OVER", "edge": 1.2, "bookmaker_count": 2},
            min_team_games=1,
        )
        strong = confidence_for_prediction(
            {"sport": "NFL", "pick": "OVER", "edge": 4.0, "bookmaker_count": 8},
            min_team_games=6,
        )
        self.assertGreater(strong["confidence_score"], thin["confidence_score"])
        self.assertLessEqual(strong["confidence_score"], 95)

    def test_nba_pregame_previous_season_baseline_is_low_confidence(self):
        base = {
            "sport": "NBA", "pick": "OVER", "edge": 15.0,
            "bookmaker_count": 9,
            "model_version": "nba_regular_season_blend_v1",
            "nba_current_games": 0, "nba_prior_fraction": 1.0,
        }
        opening = confidence_for_prediction(base, min_team_games=0)
        five_games = confidence_for_prediction(
            {**base, "nba_current_games": 5, "nba_prior_fraction": 0.5},
            min_team_games=5,
        )
        ten_games = confidence_for_prediction(
            {**base, "nba_current_games": 10, "nba_prior_fraction": 0.0},
            min_team_games=10,
        )
        self.assertLessEqual(opening["confidence_score"], 55)
        self.assertEqual(opening["confidence_label"], "Developing")
        self.assertIn("100% prior-season baseline", opening["confidence_note"])
        self.assertLessEqual(five_games["confidence_score"], 70)
        self.assertLessEqual(ten_games["confidence_score"], 95)

    def test_mlb_v2_gets_small_model_quality_bonus(self):
        base = {"sport": "MLB", "pick": "UNDER", "edge": -0.6, "bookmaker_count": 4}
        baseline = confidence_for_prediction(base, min_team_games=10)
        v2 = confidence_for_prediction(
            {**base, "model_version": "mlb_form_pitching_v2"},
            min_team_games=10,
        )
        self.assertGreater(v2["confidence_score"], baseline["confidence_score"])


if __name__ == "__main__":
    unittest.main()
