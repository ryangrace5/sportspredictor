import os
import unittest
from unittest.mock import patch

from bet_policy import LEAGUE_RULES, assess_best_play, validated_leagues
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
            "edge": 1.0,
            "abs_edge": 1.0,
            "best_abs_edge": 1.5,
        }

    def candidate(self, league):
        settings = LEAGUE_RULES[league]
        return dict(
            self.good, sport=league, model_version=settings["model"],
            sample_games=settings["min_games"],
            edge=settings["min_edge"] + 0.5,
            confidence_score=max(90, settings["min_confidence"]),
            nba_current_games=10,
        )

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": ""})
    def test_mlb_v2_with_valid_evidence_is_eligible(self):
        eligible, _ = assess_best_play(self.good)
        self.assertTrue(eligible)

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": ""})
    def test_non_mlb_models_are_research_only_without_forward_validation(self):
        for league in ("NFL", "NCAAF", "NBA"):
            with self.subTest(league=league):
                allowed, reason = assess_best_play(self.candidate(league))
                self.assertFalse(allowed)
                self.assertIn("independent forward validation", reason)

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": "NFL"})
    def test_nfl_can_qualify_after_validation_without_nba_sample_requirement(self):
        candidate = self.candidate("NFL")
        self.assertEqual(candidate["sample_games"], 5)
        allowed, reason = assess_best_play(candidate)
        self.assertTrue(allowed, reason)

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": "NFL, NBA, NCAAF"})
    def test_every_league_uses_own_samples_and_model(self):
        for league in ("NBA", "NFL", "NCAAF"):
            with self.subTest(league=league):
                good = self.candidate(league)
                self.assertTrue(assess_best_play(good)[0])
                self.assertFalse(
                    assess_best_play({**good, "sample_games": LEAGUE_RULES[league]["min_games"] - 1})[0]
                )
                self.assertFalse(assess_best_play({**good, "model_version": "unknown"})[0])
                if league == "NBA":
                    self.assertFalse(assess_best_play({**good, "nba_current_games": 5})[0])

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
            {"edge": 0.1},
            {"game_started": True},
            {"pick_locked": True},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertFalse(assess_best_play(dict(self.good, **overrides))[0])

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": "NFL"})
    def test_validation_never_bypasses_market_protections(self):
        good = self.candidate("NFL")
        for broken in (
            {"market_time_verified": False},
            {"bookmaker_count": 0},
            {"best_price": None},
            {"sample_games": 3},
            {"edge": 0.5},
        ):
            with self.subTest(broken=broken):
                self.assertFalse(assess_best_play({**good, **broken})[0])

    @patch.dict(os.environ, {"BZ_VALIDATED_LEAGUES": "unknown, nfl"})
    def test_unknown_league_approval_ignored(self):
        self.assertEqual(validated_leagues(), {"MLB", "NFL"})

    def test_list_promotes_only_verified_qualified_predictions(self):
        qualified = dict(self.good, best_play_eligible=True)
        weak = dict(self.good, best_play_eligible=False, best_abs_edge=20)
        qualified2 = dict(self.good, best_play_eligible=True, bookmaker_count=7, confidence_score=77)
        ordered = select_best_bets([weak, qualified2, qualified], limit=5)
        self.assertEqual(ordered, [qualified, qualified2])


if __name__ == "__main__":
    unittest.main()
