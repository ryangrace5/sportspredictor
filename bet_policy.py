"""League-aware, evidence-gated promotion policy for BZ Bets Best Plays.

A projection is NOT a bet recommendation. League-specific minimum samples and
market checks are necessary but not sufficient: only externally validated
models may appear in Best Plays. MLB v2 is currently enabled; football and NBA
are paper-tracked until independent forward testing supports promotion.

To enable a non-MLB league *after* reviewing held-out/forward results, set
BZ_VALIDATED_LEAGUES to a comma-separated list (e.g. "NFL"). This is an
explicit operator approval, not an automatic assertion of profitability.
"""

from __future__ import annotations

import os
from math import isfinite
from typing import Any, Dict, Tuple


# These are conservative eligibility filters, NOT empirically optimized
# winning thresholds. The market edge is expressed in total points/runs.
LEAGUE_RULES: Dict[str, Dict[str, Any]] = {
    "MLB": {
        "model": "mlb_form_pitching_v2",
        "min_games": 30,
        "min_books": 5,
        "min_edge": 0.75,
        "min_confidence": 72,
    },
    "NBA": {
        "model": "nba_regular_season_blend_v1",
        "min_games": 10,  # fully transition away from previous-season baseline
        "min_books": 5,
        "min_edge": 4.0,
        "min_confidence": 82,
    },
    "NFL": {
        "model": "baseline_pfpa_v1",
        "min_games": 5,  # NFL plays 17 games: do not copy NBA sample requirements
        "min_books": 5,
        "min_edge": 3.0,
        "min_confidence": 85,
    },
    "NCAAF": {
        "model": "baseline_pfpa_v1",
        "min_games": 6,
        "min_books": 5,
        "min_edge": 4.0,
        "min_confidence": 85,
    },
}


def _number(raw: Any) -> float | None:
    try:
        value = float(raw)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def validated_leagues() -> set[str]:
    """Read explicit model approvals; an env var never bypasses safety checks."""
    extra = {
        league.strip().upper()
        for league in os.environ.get("BZ_VALIDATED_LEAGUES", "").split(",")
        if league.strip()
    }
    return {"MLB"} | (extra & set(LEAGUE_RULES))


def assess_best_play(prediction: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (eligible_for_Best_Plays, user-facing reason).

    The original market signal and paper-tracking result are not altered.
    An apparent model edge or high confidence score is NOT win probability.
    """
    if prediction.get("pick") not in {"OVER", "UNDER"}:
        return False, "No actionable market-model signal"

    if prediction.get("game_started") or prediction.get("pick_locked"):
        return False, "Game already started; pregame recommendation unavailable"

    league = str(prediction.get("sport") or "").upper()
    rules = LEAGUE_RULES.get(league)
    if not rules:
        return False, "No validated promotion policy for this league"

    if prediction.get("model_version") != rules["model"]:
        if league == "MLB":
            return False, "MLB baseline-only projections need richer inputs"
        return False, f"{league} model version not approved for Best Plays"

    games = _number(prediction.get("sample_games"))
    if games is None or games < rules["min_games"]:
        return False, f"{league} needs at least {rules['min_games']} current-season games per team"

    # A blended NBA forecast must have actual current-season games, even if
    # the Sheets G column or its model version has been accidentally mislabeled.
    if league == "NBA":
        current_games = _number(prediction.get("nba_current_games"))
        if current_games is None or current_games < rules["min_games"]:
            return False, "NBA still blending prior season; current sample too small"

    if prediction.get("market_time_verified") is not True:
        return False, "Sportsbook matchup and start time not verified"

    books = _number(prediction.get("bookmaker_count"))
    if books is None or books < rules["min_books"]:
        return False, f"Need at least {rules['min_books']} independent sportsbook lines"

    if _number(prediction.get("best_line")) is None or not prediction.get("best_book"):
        return False, "No verified best available total"

    price = _number(prediction.get("best_price"))
    if price is None or not (-125 <= price <= 125) or -100 < price < 100:
        return False, "Best available price outside conservative bounds"

    edge = _number(prediction.get("edge"))
    if edge is None or abs(edge) < rules["min_edge"]:
        return False, f"{league} edge below {rules['min_edge']:.2g}-point/runs threshold"

    confidence = _number(prediction.get("confidence_score"))
    if confidence is None or confidence < rules["min_confidence"]:
        return False, f"{league} evidence score below {rules['min_confidence']}/100"

    if league not in validated_leagues():
        return False, f"{league} model is research-only pending independent forward validation"

    return True, "Meets league model, season sample, market, price and validation checks"
