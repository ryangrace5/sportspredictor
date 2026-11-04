"""Evaluate archived pregame BZ Bets totals against the same games' sportsbook lines.

This is a *snapshot evaluation*, not a reconstructed historical backtest:
predictions/market totals must already have been frozen before game start.
No feature is rebuilt using postgame or current-season aggregates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite, isclose, sqrt
from typing import Any, Dict, Iterable, List, Optional, Tuple

LEAGUES = ("NFL", "NBA")
MAX_QUOTE_AGE = timedelta(hours=2)
MIN_DESCRIPTIVE_SAMPLE = 30


def _finite_float(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _aware_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _eligible_snapshot(row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return frozen observation and a precise reason for every exclusion."""
    if (row.get("status") or "").strip().upper() != "FINAL":
        return None, "not_final"

    bz = _finite_float(row.get("bz_total"))
    market = _finite_float(row.get("market_total"))
    actual = _finite_float(row.get("actual_total"))
    if (
        bz is None or market is None or actual is None
        or bz <= 0 or market <= 0 or actual < 0
    ):
        return None, "missing_or_invalid_totals"

    kickoff = _aware_datetime(row.get("game_time_ct"))
    snapshot = _aware_datetime(row.get("snapshot_at_ct"))
    quote_at = _aware_datetime(row.get("quote_fetched_at"))
    if kickoff is None or snapshot is None or quote_at is None:
        return None, "missing_verified_timestamps"
    if not (quote_at <= snapshot < kickoff):
        return None, "not_verified_pregame"
    if snapshot - quote_at > MAX_QUOTE_AGE:
        return None, "stale_market_quote"

    # We require an explicit model identifier, so different versions are
    # never silently combined with legacy or unknown models.
    model = (row.get("model_version") or "").strip()
    if not model:
        return None, "missing_model_version"

    return {
        "league": (row.get("league") or "").upper(),
        "model_version": model,
        "game_date": row.get("game_date") or "",
        "snapshot_at": snapshot,
        "bz_total": bz,
        "market_total": market,
        "actual_total": actual,
    }, ""


def _metrics(observations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    sample = list(observations)
    count = len(sample)
    if not count:
        return {
            "count": 0,
            "bz_mae": None,
            "market_mae": None,
            "bz_rmse": None,
            "market_rmse": None,
            "bz_bias": None,
            "market_bias": None,
            "mae_advantage": None,
            "bz_more_accurate": 0,
            "market_more_accurate": 0,
            "tied": 0,
            "sample_sufficient": False,
        }

    model_errors = [p["bz_total"] - p["actual_total"] for p in sample]
    market_errors = [p["market_total"] - p["actual_total"] for p in sample]
    bz_mae = sum(abs(e) for e in model_errors) / count
    market_mae = sum(abs(e) for e in market_errors) / count
    beats = sum(
        abs(a) < abs(b) and not isclose(abs(a), abs(b), abs_tol=1e-9)
        for a, b in zip(model_errors, market_errors)
    )
    losses = sum(
        abs(b) < abs(a) and not isclose(abs(a), abs(b), abs_tol=1e-9)
        for a, b in zip(model_errors, market_errors)
    )
    return {
        "count": count,
        "bz_mae": round(bz_mae, 2),
        "market_mae": round(market_mae, 2),
        "bz_rmse": round(sqrt(sum(e * e for e in model_errors) / count), 2),
        "market_rmse": round(sqrt(sum(e * e for e in market_errors) / count), 2),
        "bz_bias": round(sum(model_errors) / count, 2),
        "market_bias": round(sum(market_errors) / count, 2),
        # Positive => the BZ model has a smaller error than the market.
        "mae_advantage": round(market_mae - bz_mae, 2),
        "bz_more_accurate": beats,
        "market_more_accurate": losses,
        "tied": count - beats - losses,
        "sample_sufficient": count >= MIN_DESCRIPTIVE_SAMPLE,
    }


def evaluate_pregame_snapshots(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize pregame NFL/NBA forecasts, excluding unverified history.

    The caller should pass canonical/deduplicated historical tracking rows.
    The metrics describe archived forecasts, not an out-of-sample proof of
    model calibration, expected betting returns, or market profitability.
    """
    all_rows = [r for r in rows if (r.get("league") or "").upper() in LEAGUES]
    valid: List[Dict[str, Any]] = []
    excluded: Dict[str, int] = {}
    per_league_excluded: Dict[str, Dict[str, int]] = {league: {} for league in LEAGUES}

    for row in all_rows:
        entry, reason = _eligible_snapshot(row)
        if entry is not None:
            valid.append(entry)
        else:
            excluded[reason] = excluded.get(reason, 0) + 1
            league = (row.get("league") or "").upper()
            counter = per_league_excluded[league]
            counter[reason] = counter.get(reason, 0) + 1

    league_results = []
    for league in LEAGUES:
        league_valid = [item for item in valid if item["league"] == league]
        league_all = [item for item in all_rows if (item.get("league") or "").upper() == league]
        versions = sorted({item["model_version"] for item in league_valid})
        league_results.append({
            "league": league,
            "tracked": len(league_all),
            "excluded": len(league_all) - len(league_valid),
            **_metrics(league_valid),
            "versions": [
                {"version": version, **_metrics(
                    item for item in league_valid if item["model_version"] == version
                )}
                for version in versions
            ],
        })

    return {
        "tracked": len(all_rows),
        "eligible": len(valid),
        "excluded": len(all_rows) - len(valid),
        "exclusion_reasons": excluded,
        "leagues": league_results,
        "method": "Frozen, verified pregame snapshots only; no historical refitting",
        "min_descriptive_sample": MIN_DESCRIPTIVE_SAMPLE,
    }
