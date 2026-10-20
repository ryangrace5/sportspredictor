import unittest
from bet_policy import assess_best_play
from odds_service import select_best_bets


class ConservativeBestPlaysTests(unittest.TestCase):
    def setUp(self):
        self.good = {
            "sport": "MLB",
            "pick": "OVER",
            "model_version": "mlb_form_pitching_v2",
            "sample_games": 72,
            "market_time_verified": True,
            "bookmaker_count": 8,
            "best_line": 8.5,
            "best_book": "Test Sportsbook",
            "best_price": -110,
            "confidence_score": 86,
            "abs_edge": 1.0,
            "best_abs_edge": 1.5,
        }

    def test_v2_with_valid_evidence_is_eligible(self):
        eligible, _ = assess_best_play(self.good)
        self.assertTrue(eligible)

    def test_nfl_ncaaf_nba_remain_research_signals(self):
        for league in ("NFL", "NCAAF", "NBA"):
            with self.subTest(league=league):
                candidate = dict(self.good, sport=league)
                allowed, reason = assess_best_play(candidate)
                self.assertFalse(allowed)
                self.assertTrue(
                    "track record" in reason or "research-only" in reason
                )

    def test_baseline_only_mlb_is_not_promoted(self):
        allowed, reason = assess_best_play(
            dict(self.good, model_version="baseline_pfpa_v1")
        )
        self.assertFalse(allowed)
        self.assertIn("baseline", reason)

    def test_market_and_pricing_issues_block_promotion(self):
        cases = (
            {"market_time_verified": False},
            {"bookmaker_count": 3},
            {"best_price": -150},
            {"best_price": None},
            {"best_line": None},
            {"sample_games": 10},
            {"confidence_score": 55},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertFalse(assess_best_play(dict(self.good, **overrides))[0])

    def test_list_promotes_only_verified_qualified_predictions(self):
        qualified = dict(self.good, best_play_eligible=True)
        weak = dict(self.good, best_play_eligible=False, best_abs_edge=20)
        qualified2 = dict(self.good, best_play_eligible=True, bookmaker_count=7, confidence_score=77)
        ordered = select_best_bets([weak, qualified2, qualified], limit=5)
        self.assertEqual(ordered, [qualified, qualified2])


if __name__ == "__main__":
    unittest.main()
