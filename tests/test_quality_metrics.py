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
