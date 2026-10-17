"""ESPN fantasy basketball league sync (unofficial API used by the ESPN website).

Public leagues need only the league ID. Private leagues also need the `espn_s2`
and `SWID` cookies from a logged-in browser session on fantasy.espn.com.
ESPN names seasons by the year they end in, same as this app (2027 = 2026-27).
"""
import json
from datetime import datetime, timezone

import requests

from ..names import norm_name

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{league_id}"

PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GSW",
    10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK",
    19: "ORL", 20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS",
    28: "TOR", 29: "MEM", 30: "CHA",
}
SLOT_POS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}


def _positions(player: dict) -> str:
    slots = player.get("eligibleSlots") or []
    return ",".join(SLOT_POS[s] for s in sorted(set(slots)) if s in SLOT_POS)


def _team_name(t: dict) -> str:
    return t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or t.get("abbrev", "")


def sync(cfg: dict) -> dict:
    lc = cfg["league"]
    if not lc.get("league_id"):
        raise ValueError("Set league.league_id in config.json")
    url = BASE.format(season=cfg["season"], league_id=lc["league_id"])
    cookies = {}
    if lc.get("espn_s2") and lc.get("swid"):
        cookies = {"espn_s2": lc["espn_s2"], "SWID": lc["swid"]}

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    s.cookies.update(cookies)

    r = s.get(url, params=[("view", "mTeam"), ("view", "mSettings"), ("view", "mDraftDetail")], timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError("ESPN rejected the request: private leagues need espn_s2 and swid in config.json")
    r.raise_for_status()
    league = r.json()

    swid = (lc.get("swid") or "").upper()
    teams = []
    for t in league.get("teams", []):
        owners = [o.upper() for o in t.get("owners", []) if isinstance(o, str)]
        teams.append({"id": str(t["id"]), "name": _team_name(t), "mine": bool(swid and swid in owners)})

    # The player pool view returns rostered players and free agents, with ADP.
    fantasy_filter = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
            "limit": 600,
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
        }
    }
    r = s.get(url, params={"view": "kona_player_info"},
              headers={"x-fantasy-filter": json.dumps(fantasy_filter)}, timeout=30)
    r.raise_for_status()
    players = []
    for entry in r.json().get("players", []):
        p = entry.get("player", {})
        owner = entry.get("onTeamId") or 0
        players.append({
            "key": norm_name(p.get("fullName", "")),
            "name": p.get("fullName", ""),
            "team": PRO_TEAMS.get(p.get("proTeamId"), ""),
            "pos": _positions(p),
            "owner": str(owner) if owner else None,
            "adp": (p.get("ownership") or {}).get("averageDraftPosition") or None,
            "injury": p.get("injuryStatus") if p.get("injuryStatus") not in (None, "ACTIVE") else None,
        })

    draft = league.get("draftDetail") or {}
    by_id = {e.get("id"): norm_name(e.get("player", {}).get("fullName", "")) for e in r.json().get("players", [])}
    picks = [
        {"pick": pk.get("overallPickNumber"), "team": str(pk.get("teamId")), "key": by_id.get(pk.get("playerId"))}
        for pk in draft.get("picks", []) if pk.get("playerId", -1) > 0
    ]
    settings = league.get("settings") or {}
    return {
        "platform": "espn",
        "league_name": settings.get("name", f"ESPN league {lc['league_id']}"),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
        "players": players,
        "draft": {"in_progress": bool(draft.get("inProgress")), "picks": picks},
    }
