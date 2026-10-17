import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    # Seasons are named by the year they end in: 2027 = the 2026-27 season.
    "season": 2027,
    "history": [2026, 2025, 2024],
    "history_weights": [1.0, 0.6, 0.4],
    # Blend in stats from the season being projected once games have been played.
    "include_current_season": True,
    "season_games": 82,
    # Lower this during the season for rest-of-season projections.
    "games_remaining": 82,
    # Teams likely to be out of contention by March, which tend to shut down veterans and give
    # young players more minutes right when H2H fantasy playoffs happen. From 2026-27 preseason
    # win totals (roughly 30 or fewer); revisit at the trade deadline.
    "tank_teams": ["SAC", "BKN", "MIL", "CHI", "MEM", "LAC", "NOP"],
    # Fit each team's minutes into 240/game. Off: the 2025-26 backtest showed it hurt rankings.
    "team_minutes_budget": False,
    "league": {
        "platform": "espn",  # "espn" or "yahoo"
        "league_id": "",
        "espn_s2": "",
        "swid": "",
    },
    "yahoo": {"client_id": "", "client_secret": "", "league_id": ""},
}


def load_config() -> dict:
    """Defaults, then config.json, then the FANTASY_CONFIG env var (JSON, for hosts like Railway)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    layers = []
    if CONFIG_PATH.exists():
        layers.append(json.loads(CONFIG_PATH.read_text()))
    if os.environ.get("FANTASY_CONFIG"):
        layers.append(json.loads(os.environ["FANTASY_CONFIG"]))
    for user in layers:
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
