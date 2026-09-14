import unittest
from datetime import date
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
