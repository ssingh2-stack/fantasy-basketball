"""Download season totals from Basketball-Reference into data/raw/totals_<season>.csv.

Seasons are named by the year they end in: 2026 = the 2025-26 season.
Basketball-Reference rate-limits to ~20 requests/minute, so we sleep between pages.
"""
import csv
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
    resp = requests.get(URL.format(season=season), headers=HEADERS, timeout=30)
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


def fetch_seasons(seasons: list[int], force: bool = False) -> None:
    for i, season in enumerate(seasons):
        if i:
            time.sleep(4)
        print(f"Fetching {season - 1}-{str(season)[2:]} totals")
        try:
            fetch_season(season, force=force)
        except Exception as e:
            # Keep going: older seasons may already be on disk (or committed to the repo).
            print(f"  failed to fetch {season}: {e}")


def fetch_all(cfg: dict, force: bool = False) -> None:
    """Download the history seasons (cached) and the in-progress season (always refreshed)."""
    if cfg.get("include_current_season"):
        fetch_seasons([cfg["season"]], force=True)
        time.sleep(4)
    fetch_seasons(cfg["history"], force=force)
