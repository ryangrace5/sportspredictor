import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List

import gspread
import pytz
import requests
from google.oauth2.service_account import Credentials
from requests.adapters import HTTPAdapter, Retry


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.environ.get(
    "GOOGLE_SHEETS_ID", "1ub_a9jetvc9BB6paGVIQ_0N_ETXLMEG43tD7zeE3Ljg"
)

ESPN_STANDINGS_URLS = {
    "MLB": "https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings",
    "NBA": "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings",
    "NFL": "https://site.api.espn.com/apis/v2/sports/football/nfl/standings",
    "NCAAF": "https://site.api.espn.com/apis/v2/sports/football/college-football/standings",
}

FOOTBALL_SCOREBOARD_URLS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NCAAF": (
        "https://site.api.espn.com/apis/site/v2/sports/football/"
        "college-football/scoreboard"
    ),
}

FOOTBALL_TEAMS_URLS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams",
    "NCAAF": (
        "https://site.api.espn.com/apis/site/v2/sports/football/"
        "college-football/teams"
    ),
}

FOOTBALL_FIRST_WEEK = {"NFL": 1, "NCAAF": 0}
BODY_RANGE = "A2:D1000"
HEADER_RANGE = "A1:D1"
RECORD_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?\s*$")
TEAM_LOGOS_PATH = os.path.join(os.path.dirname(__file__), "team_logos.json")


def _load_credentials() -> Credentials:
    blob = os.environ.get("GOOGLE_CREDS_JSON")
    if blob:
        return Credentials.from_service_account_info(json.loads(blob), scopes=SCOPES)

    secret = "/etc/secrets/service_account.json"
    if os.path.exists(secret):
        return Credentials.from_service_account_file(secret, scopes=SCOPES)

    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac and os.path.exists(gac):
        return Credentials.from_service_account_file(gac, scopes=SCOPES)

    local = os.path.join(os.path.dirname(__file__), "service_account.json")
    if os.path.exists(local):
        return Credentials.from_service_account_file(local, scopes=SCOPES)

    raise RuntimeError("No Google credentials found for espn_scraper.")


def _http() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; BZBets/2.0; "
                "+https://github.com/vzjavi/sportspredictor)"
            ),
            "Accept": "application/json",
        }
    )
    retries = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _record_games(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    match = RECORD_RE.match(value)
    if not match:
        return 0
    return sum(int(part or 0) for part in match.groups())


def _stat_value(stat: Dict[str, Any]) -> Any:
    value = stat.get("value")
    if value in (None, "", "-", "—"):
        value = stat.get("displayValue")
    return value


def _extract_pf_pa_g(entry: Dict[str, Any]):
    pf = pa = games = wins = losses = ties = 0

    for stat in entry.get("stats") or []:
        name = (stat.get("name") or "").lower()
        abbr = (stat.get("abbreviation") or "").upper()
        value = _stat_value(stat)

        if (
            name in {"pointsfor", "runsfor", "overallpointsfor"}
            or abbr in {"PF", "RF"}
            or ("points" in name and ("for" in name or "scored" in name))
        ):
            pf = max(pf, _to_int(value))
        elif (
            name in {"pointsagainst", "runsagainst", "overallpointsagainst"}
            or abbr in {"PA", "RA"}
            or ("points" in name and ("against" in name or "allowed" in name))
        ):
            pa = max(pa, _to_int(value))

        if name in {"gamesplayed", "games", "overallgamesplayed"} or abbr == "GP":
            games = max(games, _to_int(value))
        elif name in {"wins", "overallwins"} or abbr == "W":
            wins = max(wins, _to_int(value))
        elif name in {"losses", "overalllosses"} or abbr == "L":
            losses = max(losses, _to_int(value))
        elif name in {"ties", "overallties"} or abbr == "T":
            ties = max(ties, _to_int(value))

        games = max(games, _record_games(stat.get("displayValue")))

    record_blocks = []
    if isinstance(entry.get("record"), dict):
        record_blocks.append(entry["record"])
    if isinstance(entry.get("records"), list):
        record_blocks.extend(entry["records"])

    for record in record_blocks:
        games = max(games, _record_games(record.get("summary")))
        for stat in record.get("stats") or []:
            name = (stat.get("name") or "").lower()
            abbr = (stat.get("abbreviation") or "").upper()
            value = _stat_value(stat)
            if name == "wins" or abbr == "W":
                wins = max(wins, _to_int(value))
            elif name == "losses" or abbr == "L":
                losses = max(losses, _to_int(value))
            elif name == "ties" or abbr == "T":
                ties = max(ties, _to_int(value))
            elif name in {"gamesplayed", "games"} or abbr == "GP":
                games = max(games, _to_int(value))
            games = max(games, _record_games(stat.get("displayValue")))

    if games == 0 and (wins or losses or ties):
        games = wins + losses + ties

    return pf, pa, games


def _standings_entries(data: Dict[str, Any]):
    entries = []
    for child in data.get("children") or []:
        entries.extend((child.get("standings") or {}).get("entries") or [])
    if not entries:
        entries.extend((data.get("standings") or {}).get("entries") or [])
    return entries


def _fetch_standings_rows(
    league: str, session: requests.Session | None = None
) -> List[List[Any]]:
    logging.info("Fetching ESPN standings JSON for %s...", league)
    session = session or _http()
    response = session.get(ESPN_STANDINGS_URLS[league], timeout=20)
    response.raise_for_status()

    rows_by_team: Dict[str, List[Any]] = {}
    for entry in _standings_entries(response.json() or {}):
        team = entry.get("team") or {}
        name = (
            team.get("displayName")
            or team.get("shortDisplayName")
            or team.get("name")
        )
        if not name:
            continue

        pf, pa, games = _extract_pf_pa_g(entry)
        current = rows_by_team.get(name)
        incoming = [name, games, pf, pa]

        if not current or games > current[1]:
            rows_by_team[name] = incoming
        else:
            current[2] = current[2] or pf
            current[3] = current[3] or pa

    rows = list(rows_by_team.values())
    logging.info("Retrieved standings data for %s %s teams.", len(rows), league)
    return rows


def _football_params(league: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": 1000}
    if league == "NCAAF":
        params["groups"] = 80
    return params


def _extract_team_catalog(data: Dict[str, Any]) -> List[str]:
    names = []
    for sport in data.get("sports") or []:
        for league in sport.get("leagues") or []:
            for item in league.get("teams") or []:
                team = item.get("team") or {}
                name = (
                    team.get("displayName")
                    or team.get("shortDisplayName")
                    or team.get("name")
                )
                if name and name not in names:
                    names.append(name)
    return names


def _fetch_football_team_catalog(
    league: str, session: requests.Session
) -> List[str]:
    params = _football_params(league)
    params["limit"] = 500 if league == "NCAAF" else 100
    response = session.get(
        FOOTBALL_TEAMS_URLS[league], params=params, timeout=20
    )
    response.raise_for_status()
    names = _extract_team_catalog(response.json() or {})
    logging.info("%s team catalog returned %s teams.", league, len(names))
    return names


def _local_ncaaf_team_catalog() -> List[str]:
    """Return the curated NCAAF team names already maintained by BZ Bets.

    ESPN's generic college-football teams endpoint is useful for discovering
    FCS opponents, but it is not reliable enough to be the sole source of the
    FBS roster. Seeding from team_logos.json guarantees known BZ Bets teams
    such as Texas Tech remain present even when ESPN's catalog omits them.
    """
    try:
        with open(TEAM_LOGOS_PATH, "r", encoding="utf-8") as handle:
            logo_data = json.load(handle)
        names = sorted((logo_data.get("NCAAF") or {}).keys())
        logging.info("Local NCAAF catalog returned %s teams.", len(names))
        return names
    except Exception as exc:
        logging.warning("Local NCAAF catalog load failed: %s", exc)
        return []


def _score_value(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    if value in (None, ""):
        return None
    score = _to_int(value, -1)
    return score if score >= 0 else None


def _scoreboard_week_number(data: Dict[str, Any]) -> int | None:
    week = data.get("week")
    if isinstance(week, dict) and week.get("number") is not None:
        number = _to_int(week.get("number"), -1)
        if number >= 0:
            return number

    for event in data.get("events") or []:
        event_week = event.get("week")
        if isinstance(event_week, dict) and event_week.get("number") is not None:
            number = _to_int(event_week.get("number"), -1)
            if number >= 0:
                return number

    return None


def _is_completed(event: Dict[str, Any], competition: Dict[str, Any]) -> bool:
    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    if status_type.get("completed") is True:
        return True
    return (status_type.get("name") or "").upper() in {
        "STATUS_FINAL",
        "STATUS_FULL_TIME",
    }


def _aggregate_scoreboard_events(
    events: Iterable[Dict[str, Any]], rows_by_team: Dict[str, List[Any]]
) -> int:
    completed_games = 0

    for event in events or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue

        competition = competitions[0]
        parsed = []
        for competitor in competition.get("competitors") or []:
            team = competitor.get("team") or {}
            name = (
                team.get("displayName")
                or team.get("shortDisplayName")
                or team.get("name")
            )
            if not name:
                continue
            rows_by_team.setdefault(name, [name, 0, 0, 0])
            parsed.append((name, _score_value(competitor.get("score"))))

        if len(parsed) != 2 or not _is_completed(event, competition):
            continue

        (team_a, score_a), (team_b, score_b) = parsed
        if score_a is None or score_b is None:
            logging.warning(
                "Skipping completed event with missing score: %s vs %s",
                team_a,
                team_b,
            )
            continue

        row_a = rows_by_team[team_a]
        row_b = rows_by_team[team_b]
        row_a[1] += 1
        row_a[2] += score_a
        row_a[3] += score_b
        row_b[1] += 1
        row_b[2] += score_b
        row_b[3] += score_a
        completed_games += 1

    return completed_games


def fetch_football_scoreboard_stats(
    league: str, session: requests.Session | None = None
) -> List[List[Any]]:
    if league not in FOOTBALL_SCOREBOARD_URLS:
        raise ValueError(f"{league} is not a football scoreboard league")

    session = session or _http()
    rows_by_team: Dict[str, List[Any]] = {}

    # Always seed BZ Bets' curated NCAAF list first. ESPN's catalog is then
    # merged in so FCS opponents and newly discovered teams are not lost.
    if league == "NCAAF":
        for name in _local_ncaaf_team_catalog():
            rows_by_team[name] = [name, 0, 0, 0]

    try:
        for name in _fetch_football_team_catalog(league, session):
            rows_by_team.setdefault(name, [name, 0, 0, 0])
    except Exception as exc:
        logging.warning("%s team catalog fetch failed: %s", league, exc)

    params = _football_params(league)
    current_response = session.get(
        FOOTBALL_SCOREBOARD_URLS[league], params=params, timeout=20
    )
    current_response.raise_for_status()
    current_data = current_response.json() or {}

    current_week = _scoreboard_week_number(current_data)
    if current_week is None:
        raise RuntimeError(f"Could not determine current ESPN week for {league}")

    completed_games = 0

    # Fetch every regular-season week explicitly, including the current week.
    # ESPN's default scoreboard response can be date-scoped and may omit games
    # that were played earlier in the same week (the Texas Tech case).
    for week in range(FOOTBALL_FIRST_WEEK[league], current_week + 1):
        week_params = dict(params)
        week_params.update({"week": week, "seasontype": 2})
        response = session.get(
            FOOTBALL_SCOREBOARD_URLS[league], params=week_params, timeout=20
        )
        response.raise_for_status()
        completed_games += _aggregate_scoreboard_events(
            (response.json() or {}).get("events") or [], rows_by_team
        )

    rows = sorted(rows_by_team.values(), key=lambda row: str(row[0]).lower())
    teams_with_games = sum(1 for row in rows if _to_int(row[1]) > 0)
    logging.info(
        "%s scoreboard stats: %s teams, %s with games, %s completed games "
        "aggregated through week %s.",
        league,
        len(rows),
        teams_with_games,
        completed_games,
        current_week,
    )
    return rows


def fetch_espn_standings(league: str) -> List[List[Any]]:
    if league in FOOTBALL_SCOREBOARD_URLS:
        try:
            return fetch_football_scoreboard_stats(league)
        except Exception as exc:
            logging.exception(
                "%s scoreboard aggregation failed; trying standings fallback: %s",
                league,
                exc,
            )

    return _fetch_standings_rows(league)


def _open_worksheet(spreadsheet, title: str):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows="1000", cols="10")


def _validate_rows_for_sheet(
    league: str, rows: Iterable[Iterable[Any]]
) -> List[List[Any]]:
    normalized = [list(row) for row in rows]

    if not normalized:
        raise ValueError(f"Refusing to overwrite {league}: scraper returned 0 rows")

    if any(len(row) < 4 or not str(row[0]).strip() for row in normalized):
        raise ValueError(
            f"Refusing to overwrite {league}: scraper returned malformed rows"
        )

    if league in FOOTBALL_SCOREBOARD_URLS:
        has_stats = any(
            _to_int(row[1]) > 0
            or _to_int(row[2]) > 0
            or _to_int(row[3]) > 0
            for row in normalized
        )
        if not has_stats:
            raise ValueError(
                f"Refusing to overwrite {league}: all football stats are zero"
            )

    return normalized


def update_google_sheet(league: str, rows: Iterable[Iterable[Any]]):
    rows = _validate_rows_for_sheet(league, rows)
    logging.info("Updating Google Sheet for %s with %s rows...", league, len(rows))

    credentials = _load_credentials()
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = _open_worksheet(spreadsheet, league)

    # Validate first, clear second. A bad API response can no longer wipe a
    # previously good sheet.
    worksheet.update(HEADER_RANGE, [["Team", "G", "PF", "PA"]])
    worksheet.batch_clear([BODY_RANGE])
    worksheet.update("A2", rows)

    try:
        now_ct = datetime.now(pytz.timezone("America/Chicago")).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    except Exception:
        now_ct = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    worksheet.update("F1", [[f"Last updated: {now_ct}"]])
    logging.info("✅ %s sheet updated with %s teams.", league, len(rows))


def run_scraper():
    summary: Dict[str, int] = {}

    for league in ["MLB", "NBA", "NFL", "NCAAF"]:
        try:
            rows = fetch_espn_standings(league)
            update_google_sheet(league, rows)
            summary[league] = len(rows)
        except Exception as exc:
            logging.exception("❌ %s scraping failed: %s", league, exc)
            summary[league] = 0

    return summary


if __name__ == "__main__":
    run_scraper()
