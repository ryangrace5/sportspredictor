"""Replay NBA pace against frozen Predictions rows. See --help for inputs."""
import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from urllib.request import urlopen

from backtesting_service import _eligible_snapshot

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"


def load_games(start, end, cache):
    cache.mkdir(exist_ok=True, parents=True)

    def fetch(endpoint, key):
        path = cache / (key + ".json")
        if not path.exists():
            with urlopen(BASE + endpoint + key, timeout=30) as response:
                payload = json.load(response)
            path.write_text(json.dumps(payload), encoding="utf8")
        return json.loads(path.read_text(encoding="utf8"))

    days = [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range((end-start).days+1)]
    with ThreadPoolExecutor(4) as pool:
        boards = pool.map(lambda day: fetch("scoreboard?dates=", day), days)
        ids = {e["id"] for board in boards for e in board["events"]
               if e["season"]["type"] == 2 and e["status"]["type"]["completed"]}
        return [parse_box(box) for box in pool.map(lambda i: fetch("summary?event=", i), sorted(ids))]


def parse_box(box):
    comp = box["header"]["competitions"][0]
    teams = {t["team"]["displayName"]: int(t["score"]) for t in comp["competitors"]}
    estimates = []
    for team in box["boxscore"]["teams"]:
        stats = {x["name"]: x["displayValue"] for x in team["statistics"]}
        fga = int(stats["fieldGoalsMade-fieldGoalsAttempted"].split("-")[1])
        fta = int(stats["freeThrowsMade-freeThrowsAttempted"].split("-")[1])
        estimates.append(fga + 0.44 * fta - int(stats["offensiveRebounds"]) + int(stats["totalTurnovers"]))
    if len(teams) != 2 or len(estimates) != 2 or min(estimates) <= 0:
        raise ValueError(f"Incomplete team box score for game {comp['id']}")
    # Require the final play's wall clock so an unfinished game cannot enter a snapshot.
    end = datetime.fromisoformat(box["plays"][-1]["wallclock"].replace("Z", "+00:00"))
    return teams, mean(estimates), end, datetime.fromisoformat(comp["date"].replace("Z", "+00:00"))


def compare(rows, games, start, end):
    results = []
    seen = set()
    for row in sorted(rows, key=lambda r: r.get("snapshot_at_ct") or ""):
        obs, _ = _eligible_snapshot(row)
        if (not obs or obs["league"] != "NBA" or obs["model_version"] != "nba_regular_season_blend_v1"
                or not start <= date.fromisoformat(obs["game_date"]) <= end):
            continue
        away, home = row["away_team_raw"], row["home_team_raw"]
        key = (obs["game_date"], away, home)
        if key in seen:
            continue
        kickoff = datetime.fromisoformat(row["game_time_ct"])
        matches = [scores for scores, _, _, tip in games if set(scores) == {away, home}
                   and abs(tip - kickoff) < timedelta(hours=1)]
        if len(matches) != 1 or sum(matches[0].values()) != obs["actual_total"]:
            continue
        history = defaultdict(list)
        for scores, poss, finished, _ in games:
            if finished >= obs["snapshot_at"]:
                continue
            for team in (away, home):
                if team in scores:
                    history[team].append((scores[team], sum(scores.values())-scores[team], poss))
        # At ten games the production baseline uses only the current season.
        # Restrict both models to that regime rather than substitute a different prior.
        if min(len(history[away]), len(history[home])) < 10:
            continue

        def rates(team):
            scored, allowed, possessions = map(sum, zip(*history[team]))
            return possessions / len(history[team]), scored / possessions, allowed / possessions

        a, h = rates(away), rates(home)
        pace = (a[0]+h[0])/2
        total = pace * (a[1]+h[2]+h[1]+a[2])/2
        results.append(dict(date=key[0], away=away, home=home, actual=obs["actual_total"],
                            baseline=obs["bz_total"], pace=round(total, 1), market=obs["market_total"]))
        seen.add(key)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay one NBA regular season. Input is a Predictions CSV export or JSON list of header-keyed rows.")
    parser.add_argument("archive", type=Path)
    for field in ("season-start", "test-start", "test-end"):
        parser.add_argument("--"+field, type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.season_start <= args.test_start <= args.test_end:
        parser.error("Expected season start <= test start <= test end.")
    with args.archive.open(encoding="utf-8-sig", newline="") as f:
        rows = json.load(f) if args.archive.suffix == ".json" else list(csv.DictReader(f))
    results = compare(rows, load_games(args.season_start, args.test_end, args.cache), args.test_start, args.test_end)
    with args.output.open("w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=["date","away","home","actual","baseline","pace","market"])
        writer.writeheader()
        writer.writerows(results)
    for model in ("baseline", "pace", "market"):
        errors = [r[model]-r["actual"] for r in results]
        if errors:
            print(f"{model}: {len(errors)} games, MAE {mean(map(abs,errors)):.2f}, bias {mean(errors):+.2f}")
    if not results:
        print("No eligible matched games.")
