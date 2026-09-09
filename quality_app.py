"""BZ Bets production enhancements layered on top of the existing Flask app.

Keeping these improvements in a small integration module lets the current app
stay stable while we add data-integrity diagnostics and confidence scoring.
"""

from __future__ import annotations

import logging
from html import escape
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, render_template

import app as legacy
from quality_metrics import confidence_for_prediction

app = legacy.app
SPORTS = ("NBA", "MLB", "NFL", "NCAAF")
_ORIGINAL_PREDICT = legacy.predict_game_totals


def _minimum_team_games(league: str, prediction: Dict[str, Any], stats_df) -> Optional[float]:
    if stats_df is None or stats_df.empty:
        return None

    games = []
    for team in (prediction.get("team1"), prediction.get("team2")):
        if not team or team not in stats_df.index:
            continue
        try:
            games.append(float(stats_df.loc[team].get("G", 0) or 0))
        except Exception:
            continue
    return min(games) if games else None


def _add_confidence(predictions: List[Dict[str, Any]], league: str) -> List[Dict[str, Any]]:
    try:
        stats_df = legacy.fetch_data_from_sheets(league)
    except Exception as exc:
        logging.warning("Confidence sample lookup failed for %s: %s", league, exc)
        stats_df = None

    for prediction in predictions:
        min_games = _minimum_team_games(league, prediction, stats_df)
        prediction["sample_games"] = min_games
        prediction.update(confidence_for_prediction(prediction, min_games))
    return predictions


def predict_game_totals_with_quality(league_name: str):
    predictions = _ORIGINAL_PREDICT(league_name)
    return _add_confidence(predictions, league_name)


predict_game_totals_with_quality._bz_quality_wrapped = True  # type: ignore[attr-defined]
legacy.predict_game_totals = predict_game_totals_with_quality


_ORIGINAL_RENDER_TEMPLATE = legacy.render_template


def _confidence_panel(best_bets: List[Dict[str, Any]]) -> str:
    playable = [bet for bet in best_bets if bet.get("confidence_score") is not None]
    if not playable:
        return ""

    cards = []
    for bet in playable:
        matchup = f"{bet.get('team1') or ''} @ {bet.get('team2') or ''}"
        score = int(bet.get("confidence_score"))
        label = escape(str(bet.get("confidence_label") or ""))
        note = escape(str(bet.get("confidence_note") or ""))
        pick = escape(str(bet.get("pick") or ""))
        line = bet.get("best_line") if bet.get("best_line") is not None else bet.get("market_total")
        line_text = f" {float(line):.1f}" if line is not None else ""
        cards.append(
            '<article class="best-bet-card">'
            '<div class="best-bet-top">'
            f'<span class="league-badge">{escape(str(bet.get("sport") or ""))}</span>'
            f'<span class="edge-tier">BZ Confidence {score}/100</span>'
            '</div>'
            f'<div class="best-bet-matchup">{escape(matchup)}</div>'
            f'<div class="best-bet-pick {pick.lower()}">{pick}{line_text}</div>'
            '<div class="best-book-callout"><small>Signal</small>'
            f'<strong>{label}</strong><span>{note}</span></div>'
            '</article>'
        )

    return (
        '<section class="best-bets" aria-labelledby="confidenceHeading">'
        '<div class="section-heading"><div>'
        '<span class="eyebrow">Model quality signal</span>'
        '<h2 id="confidenceHeading">BZ Confidence</h2></div>'
        '<span class="section-note">0-100 evidence score based on edge, team sample size, and market depth. It is not a win probability.</span>'
        '</div><div class="best-bet-grid">'
        + ''.join(cards)
        + '</div></section>'
    )


def render_template_with_quality(template_name: str, *args, **kwargs):
    rendered = _ORIGINAL_RENDER_TEMPLATE(template_name, *args, **kwargs)
    if template_name != "index.html":
        return rendered

    # Add Data Health navigation without forcing a risky rewrite of the existing
    # page template. The prediction objects already include confidence fields.
    if 'href="/data-health"' not in rendered:
        rendered = rendered.replace(
            '</nav>',
            '<a href="/data-health">Data Health</a></nav>',
            1,
        )

    panel = _confidence_panel(list(kwargs.get("best_bets") or []))
    if panel:
        rendered = rendered.replace('<main class="card">', panel + '<main class="card">', 1)
    return rendered


legacy.render_template = render_template_with_quality


def _match_schedule_team(
    league: str,
    raw_name: str,
    team_list: List[str],
    stats_df,
) -> Tuple[Optional[str], Any]:
    if league == "NCAAF":
        return legacy._ncaaf_strict_match(raw_name, team_list, stats_df)

    matched = legacy._pro_strict_match(raw_name, team_list)
    if not matched:
        return None, None
    try:
        return matched, stats_df.loc[matched]
    except Exception:
        return None, None


def sport_data_health(league: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "league": league,
        "status": "healthy",
        "schedule_games": 0,
        "schedule_teams": 0,
        "sheet_teams": 0,
        "matched_teams": 0,
        "missing_teams": [],
        "zero_sample_teams": [],
        "error": None,
    }

    try:
        games = legacy.get_todays_games(league)
        stats_df = legacy.fetch_data_from_sheets(league)
        team_list = stats_df.index.tolist()
        schedule_teams = sorted(
            {
                name
                for game in games
                for name in (game.get("strAwayTeam"), game.get("strHomeTeam"))
                if name
            }
        )

        missing = []
        zero_sample = []
        matched_count = 0
        for raw_name in schedule_teams:
            matched, row = _match_schedule_team(league, raw_name, team_list, stats_df)
            if not matched or row is None:
                missing.append(raw_name)
                continue
            matched_count += 1
            try:
                if float(row.get("G", 0) or 0) <= 0:
                    zero_sample.append(matched)
            except Exception:
                zero_sample.append(matched)

        result.update(
            {
                "schedule_games": len(games),
                "schedule_teams": len(schedule_teams),
                "sheet_teams": len(team_list),
                "matched_teams": matched_count,
                "missing_teams": sorted(set(missing)),
                "zero_sample_teams": sorted(set(zero_sample)),
            }
        )

        if missing:
            result["status"] = "warning"
        elif zero_sample:
            result["status"] = "watch"
    except Exception as exc:
        logging.exception("Data health check failed for %s", league)
        result["status"] = "error"
        result["error"] = str(exc)

    return result


def build_data_health() -> Dict[str, Any]:
    sports = [sport_data_health(sport) for sport in SPORTS]
    has_error = any(item["status"] == "error" for item in sports)
    has_warning = any(item["status"] == "warning" for item in sports)
    overall = "error" if has_error else "warning" if has_warning else "healthy"

    return {
        "overall": overall,
        "checked_at": datetime.now(legacy.LOCAL_TIMEZONE).isoformat(),
        "odds_api_enabled": legacy.odds_api_enabled(),
        "sports": sports,
    }


def enhanced_admin_health():
    health = build_data_health()
    return jsonify({"ok": health["overall"] != "error", **health})


# Replace the original minimal health view without changing its URL.
app.view_functions["admin_health"] = enhanced_admin_health


@app.get("/data-health")
def data_health():
    health = build_data_health()
    logo_url = legacy.url_for("static", filename="XHE1qwUp_400x400.jpg")
    return render_template("data_health.html", health=health, logo_url=logo_url)
