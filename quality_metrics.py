"""Quality signals for BZ Bets predictions.

The score produced here is deliberately not a win probability. It is a 0-100
quality/confidence signal based on model-vs-market edge, sample size, and market
depth so the UI can distinguish stronger model setups from thin-data plays.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

STRONG_EDGE = {
    "MLB": 1.0,
    "NBA": 4.0,
    "NFL": 3.0,
    "NCAAF": 4.0,
}

FULL_SAMPLE_GAMES = {
    "MLB": 30,
    "NBA": 15,
    "NFL": 6,
    "NCAAF": 6,
}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def confidence_for_prediction(
    prediction: Dict[str, Any],
    min_team_games: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a conservative BZ confidence signal for one prediction.

    This is not calibrated as a probability of winning. It measures how much
    evidence supports the current BZ play using edge magnitude, team sample
    size, and sportsbook depth.
    """

    league = str(prediction.get("sport") or "").upper()
    pick = str(prediction.get("pick") or "").upper()
    edge = _to_float(prediction.get("edge"))

    if pick not in {"OVER", "UNDER"} or edge is None:
        return {
            "confidence_score": None,
            "confidence_label": "No play",
            "confidence_note": "Confidence is only scored for actionable OVER/UNDER picks.",
            "confidence_components": {},
        }

    strong_edge = STRONG_EDGE.get(league, 3.0)
    edge_ratio = min(abs(edge) / max(strong_edge, 0.1), 1.25)

    target_games = FULL_SAMPLE_GAMES.get(league, 8)
    sample_games = _to_float(min_team_games)
    if sample_games is None:
        sample_ratio = 0.45
    else:
        sample_ratio = min(max(sample_games, 0.0) / target_games, 1.0)

    books = _to_float(prediction.get("bookmaker_count")) or 0.0
    market_ratio = min(books / 8.0, 1.0)

    # Start cautiously and require evidence to move higher. MLB v2 gets a small
    # boost because it includes recent form and probable starters in addition to
    # the season baseline.
    score = 43.0 + (32.0 * edge_ratio) + (15.0 * sample_ratio) + (7.0 * market_ratio)
    if prediction.get("model_version") == "mlb_form_pitching_v2":
        score += 3.0

    score = int(round(min(max(score, 50.0), 95.0)))
    if score >= 85:
        label = "High"
    elif score >= 72:
        label = "Medium"
    else:
        label = "Developing"

    note_parts = [f"edge {abs(edge):.1f}"]
    if sample_games is not None:
        note_parts.append(f"min team sample {int(sample_games)} games")
    if books:
        note_parts.append(f"{int(books)} books")

    return {
        "confidence_score": score,
        "confidence_label": label,
        "confidence_note": " · ".join(note_parts),
        "confidence_components": {
            "edge_ratio": round(edge_ratio, 3),
            "sample_ratio": round(sample_ratio, 3),
            "market_ratio": round(market_ratio, 3),
        },
    }
