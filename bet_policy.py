"""Conservative, auditable promotion policy for BZ Bets' Best Plays section.

Best Plays is a shortlist, not a guarantee or a win-probability model. All
leagues keep their projections for forward validation and paper tracking.
Only the better-established MLB v2 model is currently eligible for promotion,
based on tracked historical results (not an out-of-sample proof of profitability).
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, Tuple


def _number(raw: Any) -> float | None:
    try:
        value = float(raw)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def assess_best_play(prediction: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (eligible_for_best_plays, reason).

    This doesn't change the model's original pick or historical grading.
    Non-promoted picks remain visible as research-only signals.
    """
    if prediction.get("pick") not in {"OVER", "UNDER"}:
        return False, "No actionable market-model signal"

    league = str(prediction.get("sport") or "").upper()
    if league == "NBA":
        return False, "NBA blend has no graded track record yet"
    if league in {"NFL", "NCAAF"}:
        return False, "Football totals are research-only pending better validation"
    if league != "MLB":
        return False, "Model has not been validated for promotion"

    if prediction.get("model_version") != "mlb_form_pitching_v2":
        return False, "MLB baseline-only projections need richer inputs"

    games = _number(prediction.get("sample_games"))
    if games is None or games < 30:
        return False, "Insufficient current-season team sample"

    if prediction.get("market_time_verified") is not True:
        return False, "Sportsbook matchup and start time not verified"

    books = _number(prediction.get("bookmaker_count"))
    if books is None or books < 5:
        return False, "Need at least 5 independent sportsbook lines"

    if _number(prediction.get("best_line")) is None or not prediction.get("best_book"):
        return False, "No verified best available total"

    price = _number(prediction.get("best_price"))
    if price is None or not (-125 <= price <= 125) or -100 < price < 100:
        return False, "Best available price outside conservative bounds"

    confidence = _number(prediction.get("confidence_score"))
    if confidence is None or confidence < 72:
        return False, "Model evidence not sufficient for shortlist"

    return True, "Meets model, market, timing, sample, and price checks"
