"""Download season totals from Basketball-Reference into data/raw/totals_<season>.csv.

Seasons are named by the year they end in: 2026 = the 2025-26 season.
Basketball-Reference rate-limits to ~20 requests/minute, so we sleep between pages.
"""
import csv
import json
import time
from pathlib import Path

import requests
from lxml import html

from .names import norm_team

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
URL = "https://www.basketball-reference.com/leagues/NBA_{season}_totals.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (fantasy-hoops projections; personal use)"}

# Our column -> candidate data-stat attributes (Basketball-Reference renamed some in 2024).
FIELDS = {
    "name": ["name_display", "player"],
    "team": ["team_name_abbr", "team_id"],
    "pos": ["pos"],
    "age": ["age"],
    "g": ["games", "g"],
    "gs": ["games_started", "gs"],
    "min": ["mp"],
    "fgm": ["fg"],
    "fga": ["fga"],
    "fg3m": ["fg3"],
    "fg3a": ["fg3a"],
    "ftm": ["ft"],
    "fta": ["fta"],
    "oreb": ["orb"],
    "dreb": ["drb"],
    "treb": ["trb"],
    "ast": ["ast"],
    "stl": ["stl"],
    "blk": ["blk"],
    "tov": ["tov"],
    "pts": ["pts"],
}
COLUMNS = ["bbref_id"] + list(FIELDS)
MULTI_TEAM = {"TOT", "2TM", "3TM", "4TM", "5TM"}


class RateLimited(Exception):
    """Basketball-Reference answered 429: too many requests from this server."""


RATE_LIMIT_MSG = ("Basketball-Reference is rate-limiting this server (too many requests, often from "
                  "back-to-back deploys). The block usually lifts within an hour; the app retries on its next refresh.")


def _get(url: str, timeout: int = 30) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 429:
        raise RateLimited(RATE_LIMIT_MSG)
    return resp


def parse_totals(page: str) -> list[dict]:
    """Parse the totals table into one row per player (multi-team players are combined)."""
    tree = html.fromstring(page)
    try:
        table = tree.get_element_by_id("totals_stats")
    except KeyError:
        return []  # season page exists but no games have been played yet
    players: dict[str, dict] = {}
    for tr in table.iter("tr"):
        cells = {c.get("data-stat"): c for c in tr if c.get("data-stat")}
        name_cell = cells.get("name_display")
        if name_cell is None:
            name_cell = cells.get("player")
        if name_cell is None or not name_cell.get("data-append-csv"):
            continue  # header, spacer or league-average row
        row = {"bbref_id": name_cell.get("data-append-csv")}
        for field, keys in FIELDS.items():
            cell = next((cells[k] for k in keys if k in cells), None)
            row[field] = cell.text_content().strip() if cell is not None else ""
        row["name"] = row["name"].replace("*", "")
        pid = row["bbref_id"]
        if pid not in players:
            players[pid] = row
        elif row["team"] not in MULTI_TEAM:
            # Later single-team rows are in chronological order: keep the aggregate
            # stats but remember the most recent team.
            players[pid]["team"] = row["team"]
    for row in players.values():
        row["team"] = norm_team(row["team"])
    return list(players.values())


def fetch_season(season: int, force: bool = False) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"totals_{season}.csv"
    if out.exists() and not force:
        print(f"  {out.name} already downloaded (use --force to refresh)")
        return out
    resp = _get(URL.format(season=season))
    if resp.status_code == 404:
        print(f"  season {season} not available yet, skipping")
        return out
    resp.raise_for_status()
    # Basketball-Reference is UTF-8 but doesn't say so in its headers, so requests would
    # guess Latin-1 and mangle names like Jokić.
    rows = parse_totals(resp.content.decode("utf-8", errors="replace"))
    if not rows:
        print(f"  season {season} has no stats yet, skipping")
        return out
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(rows)} players to {out.name}")
    return out


def fetch_seasons(seasons: list[int], force: bool = False) -> list[str]:
    """Download each season; returns a description of every failure (empty = all fine)."""
    errors = []
    for i, season in enumerate(seasons):
        if i:
            time.sleep(4)
        print(f"Fetching {season - 1}-{str(season)[2:]} totals")
        try:
            fetch_season(season, force=force)
        except RateLimited:
            raise  # more requests would only extend the block
        except Exception as e:
            # Keep going: older seasons may already be on disk (or committed to the repo).
            print(f"  failed to fetch {season}: {e}")
            errors.append(f"{season}: {type(e).__name__}: {e}")
    return errors


# Basketball-Reference's own team codes, used in roster page URLs.
BBREF_TEAMS = ["ATL", "BOS", "BRK", "CHI", "CHO", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
               "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHO",
               "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]
ROSTER_URL = "https://www.basketball-reference.com/teams/{team}/{season}.html"


def parse_roster(page: str) -> list[dict]:
    """Players on a team page's roster table: bbref_id and name."""
    tree = html.fromstring(page)
    try:
        table = tree.get_element_by_id("roster")
    except KeyError:
        return []
    players = []
    for cell in table.iter("td", "th"):
        if cell.get("data-stat") == "player" and cell.get("data-append-csv"):
            name = cell.text_content().replace("(TW)", "").strip()
            players.append({"bbref_id": cell.get("data-append-csv"), "name": name})
    return players


def fetch_rosters(season: int) -> Path:
    """Current rosters for every team, so offseason trades and signings are picked up."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"rosters_{season}.csv"
    rows = []
    print(f"Fetching {season - 1}-{str(season)[2:]} rosters")
    for i, team in enumerate(BBREF_TEAMS):
        if i:
            time.sleep(3.5)
        try:
            resp = _get(ROSTER_URL.format(team=team, season=season))
            resp.raise_for_status()
            players = parse_roster(resp.content.decode("utf-8", errors="replace"))
        except RateLimited:
            print(f"  {RATE_LIMIT_MSG}")
            break
        except Exception as e:
            print(f"  failed to fetch {team} roster: {e}")
            continue
        rows += [{**p, "team": norm_team(team)} for p in players]
    if len({r["team"] for r in rows}) < len(BBREF_TEAMS):
        print(f"  only got {len({r['team'] for r in rows})} rosters; keeping any previous file")
        if out.exists():
            return out
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bbref_id", "name", "team"])
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(rows)} rostered players")
    return out


def load_rosters(season: int) -> dict[str, str]:
    """bbref_id -> current team, or {} if rosters haven't been fetched."""
    path = RAW_DIR / f"rosters_{season}.csv"
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["bbref_id"]: r["team"] for r in csv.DictReader(f)}


SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"


def parse_schedule(payload: dict) -> dict[str, list[str]]:
    """team -> sorted list of regular-season game dates (YYYY-MM-DD, US Eastern)."""
    games: dict[str, set[str]] = {}
    for day in payload.get("leagueSchedule", {}).get("gameDates", []):
        for g in day.get("games", []):
            # Regular-season game IDs start with 002 (001 = preseason, 004 = playoffs).
            if not str(g.get("gameId", "")).startswith("002"):
                continue
            date = (g.get("gameDateEst") or g.get("gameDateTimeEst") or "")[:10]
            for side in ("homeTeam", "awayTeam"):
                team = norm_team((g.get(side) or {}).get("teamTricode", ""))
                if team and date:
                    games.setdefault(team, set()).add(date)
    return {team: sorted(dates) for team, dates in games.items()}


def fetch_schedule(season: int) -> Path:
    """The NBA's published schedule, for fantasy-playoff games per team."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"schedule_{season}.json"
    print("Fetching NBA schedule")
    try:
        resp = requests.get(SCHEDULE_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        year = str(payload.get("leagueSchedule", {}).get("seasonYear", ""))
        if not year.endswith(str(season)[2:]):
            print(f"  schedule is for {year!r}, not {season - 1}-{str(season)[2:]}; skipping")
            return out
        schedule = parse_schedule(payload)
    except Exception as e:
        print(f"  failed to fetch schedule: {e}")
        return out
    out.write_text(json.dumps(schedule))
    print(f"  saved schedule for {len(schedule)} teams")
    return out


def load_schedule(season: int) -> dict[str, list[str]]:
    path = RAW_DIR / f"schedule_{season}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def fetch_stats(cfg: dict, force: bool = False) -> list[str]:
    """Season totals: the history seasons (cached) and the in-progress season (always refreshed).
    Returns download errors; raises RateLimited if Basketball-Reference blocks us."""
    errors = []
    if cfg.get("include_current_season"):
        errors += fetch_seasons([cfg["season"]], force=True)
        time.sleep(4)
    return errors + fetch_seasons(cfg["history"], force=force)


def fetch_extras(cfg: dict) -> None:
    """Current rosters (30 pages, ~2 minutes) and the NBA schedule."""
    fetch_rosters(cfg["season"])
    fetch_schedule(cfg["season"])


def fetch_all(cfg: dict, force: bool = False) -> None:
    fetch_stats(cfg, force=force)
    time.sleep(4)
    fetch_extras(cfg)
