"""Yahoo Fantasy Sports league sync (official OAuth2 API).

One-time setup: create an app at https://developer.yahoo.com/apps/create/ with
"Fantasy Sports: Read" permission and redirect URI `oob`, then put the client ID
and secret in config.json. The app's Connect page walks you through approving it.
"""
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from ..config import DATA_DIR
from ..names import norm_name, norm_team

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API = "https://fantasysports.yahooapis.com/fantasy/v2"
TOKEN_PATH = DATA_DIR / "yahoo_token.json"
REDIRECT_URI = "oob"


def auth_url(cfg: dict) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": cfg["yahoo"]["client_id"], "redirect_uri": REDIRECT_URI, "response_type": "code",
    })


def _token_request(cfg: dict, data: dict) -> dict:
    y = cfg["yahoo"]
    r = requests.post(TOKEN_URL, data={**data, "redirect_uri": REDIRECT_URI},
                      auth=(y["client_id"], y["client_secret"]), timeout=30)
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    TOKEN_PATH.write_text(json.dumps(tok))
    return tok


def exchange_code(cfg: dict, code: str) -> None:
    _token_request(cfg, {"grant_type": "authorization_code", "code": code.strip()})


def is_connected() -> bool:
    return TOKEN_PATH.exists()


def _access_token(cfg: dict) -> str:
    if not TOKEN_PATH.exists():
        raise PermissionError("Yahoo is not connected yet: use Connect Yahoo in the app")
    tok = json.loads(TOKEN_PATH.read_text())
    if time.time() >= tok.get("expires_at", 0):
        tok = _token_request(cfg, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    return tok["access_token"]


def _get(cfg: dict, path: str) -> dict:
    r = requests.get(f"{API}/{path}", params={"format": "json"},
                     headers={"Authorization": f"Bearer {_access_token(cfg)}"}, timeout=30)
    r.raise_for_status()
    return r.json()["fantasy_content"]


def _merge(items) -> dict:
    """Yahoo returns objects as lists of single-key dicts (with stray [] entries); flatten them."""
    out = {}
    for it in items if isinstance(items, list) else [items]:
        if isinstance(it, dict):
            out.update(it)
        elif isinstance(it, list):
            out.update(_merge(it))
    return out


def _collection(obj: dict, name: str) -> list:
    """Yahoo collections look like {"0": {name: ...}, "1": {...}, "count": N}."""
    return [v[name] for k, v in (obj or {}).items() if k != "count" and isinstance(v, dict) and name in v]


def _player(raw: list) -> dict:
    meta = _merge(raw[0])
    positions = [p["position"] for p in meta.get("eligible_positions", []) if isinstance(p, dict)]
    positions = [p for p in ("PG", "SG", "SF", "PF", "C") if p in positions]
    status = meta.get("status") or None
    return {
        "key": norm_name(meta.get("name", {}).get("full", "")),
        "name": meta.get("name", {}).get("full", ""),
        "team": norm_team(meta.get("editorial_team_abbr", "")),
        "pos": ",".join(positions),
        "injury": status,
        "player_key": meta.get("player_key"),
    }


def league_key(cfg: dict) -> str:
    game = _merge(_get(cfg, "game/nba")["game"])
    return f"{game['game_key']}.l.{cfg['yahoo']['league_id']}"


def sync(cfg: dict) -> dict:
    if not cfg["yahoo"].get("league_id"):
        raise ValueError("Set yahoo.league_id in config.json")
    lk = league_key(cfg)

    content = _get(cfg, f"league/{lk}/teams/roster")
    league_meta = _merge(content["league"][0])
    teams, players = [], {}
    for raw_team in _collection(content["league"][1]["teams"], "team"):
        meta = _merge(raw_team[0])
        team_id = str(meta["team_id"])
        teams.append({"id": team_id, "name": meta.get("name", ""),
                      "mine": bool(int(meta.get("is_owned_by_current_login", 0) or 0))})
        roster = _merge(raw_team[1:]).get("roster", {})
        for block in roster.values() if isinstance(roster, dict) else []:
            if isinstance(block, dict) and "players" in block:
                for raw_p in _collection(block["players"], "player"):
                    p = _player(raw_p)
                    p["owner"] = team_id
                    players[p["key"]] = p

    # Draft-ranked player pool for ADP and free agents (25 per page is Yahoo's max).
    for start in range(0, 400, 25):
        page = _get(cfg, f"league/{lk}/players;sort=AR;start={start};count=25/draft_analysis")
        raw_players = _collection(page["league"][1].get("players", {}), "player")
        if not raw_players:
            break
        for raw_p in raw_players:
            p = _player(raw_p)
            da = _merge(_merge(raw_p[1:]).get("draft_analysis", []))
            try:
                adp = float(da.get("average_pick"))
            except (TypeError, ValueError):
                adp = None
            if p["key"] in players:
                players[p["key"]]["adp"] = adp
            else:
                players[p["key"]] = {**p, "owner": None, "adp": adp}

    picks = []
    try:
        dr = _get(cfg, f"league/{lk}/draftresults")
        key_to_name = {p["player_key"]: p["key"] for p in players.values()}
        for pick in _collection(dr["league"][1].get("draft_results", {}), "draft_result"):
            if pick.get("player_key"):
                team_id = pick.get("team_key", "").rsplit(".t.", 1)[-1]
                picks.append({"pick": pick.get("pick"), "team": team_id, "key": key_to_name.get(pick["player_key"])})
    except requests.HTTPError:
        pass

    return {
        "platform": "yahoo",
        "league_name": league_meta.get("name", lk),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
        "players": list(players.values()),
        "draft": {"in_progress": league_meta.get("draft_status") == "draft", "picks": picks},
    }
