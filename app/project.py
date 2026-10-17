"""Turn past seasons into per-game projections for the upcoming season.

Model (similar in spirit to Marcel-style baseball projections):
  1. Per-minute rates for every counting stat, weighting recent seasons more
     (and the in-progress season by how many minutes have been played).
  2. Regress those rates toward league average by adding REGRESSION_MIN minutes
     of an average player, so small samples don't produce extreme projections.
  3. Apply an aging curve to the rates and to minutes.
  4. Project minutes per game and games played (from each player's availability history).
  5. Apply manual overrides from data/overrides.csv (new team, new role, injuries, rookies).
"""
import csv
import json
from pathlib import Path

from .config import DATA_DIR, load_config
from .names import fix_mojibake, norm_name, norm_team
from .scrape import RAW_DIR

COUNTING = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pts"]
NUMERIC = ["age", "g", "gs", "min"] + COUNTING
REGRESSION_MIN = 300  # minutes of league-average play mixed into every rate
MPG_PRIOR = (20.0, 5)  # regress MPG toward 20 with the weight of 5 games
GP_PRIOR_RATE = 0.85  # league-typical share of games played
# A fully missed season (e.g. Achilles tear) counts as 0 games at this fraction of its normal
# weight: a strong injury signal, but one lost year shouldn't sink the next one entirely.
MISSED_SEASON_WEIGHT = 0.5
OVERRIDES_PATH = DATA_DIR / "overrides.csv"
OUT_PATH = DATA_DIR / "projections.json"


def age_rate_mult(age: float) -> float:
    """Year-over-year multiplier on per-minute production for a player turning `age`."""
    if age <= 22:
        return 1.05
    if age <= 25:
        return 1.025
    if age <= 28:
        return 1.0
    if age <= 30:
        return 0.985
    if age <= 33:
        return 0.97
    return 0.95


def age_mpg_delta(age: float) -> float:
    if age <= 23:
        return 1.5
    if age <= 30:
        return 0.0
    if age <= 34:
        return -1.0
    return -2.0


def load_season(season: int) -> list[dict]:
    path = RAW_DIR / f"totals_{season}.csv"
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            r["name"] = fix_mojibake(r["name"])  # files saved before the encoding fix
            for k in NUMERIC:
                try:
                    r[k] = float(r[k]) if r[k] != "" else 0.0
                except ValueError:
                    r[k] = 0.0
            rows.append(r)
    return rows


def league_rates(rows: list[dict], min_minutes: float = 500) -> dict:
    pool = [r for r in rows if r["min"] >= min_minutes] or rows
    total_min = sum(r["min"] for r in pool) or 1.0
    return {c: sum(r[c] for r in pool) / total_min for c in COUNTING}


def project_player(seasons: list[tuple[float, dict]], lg: dict, target_season: int,
                   season_games: int, games_remaining: int, missed: list[float] = ()) -> dict:
    """seasons: list of (weight, row) newest first; missed: weights of completed seasons
    the player sat out entirely. Returns a per-game projection."""
    wmin = sum(w * r["min"] for w, r in seasons)
    rates = {
        c: (sum(w * r[c] for w, r in seasons) + REGRESSION_MIN * lg[c]) / (wmin + REGRESSION_MIN)
        for c in COUNTING
    }

    latest_w, latest = seasons[0]
    # Age every year since the player last played, not just one (matters after a missed season).
    last_age = latest["age"] or 26
    years = max(1, target_season - int(latest["season"]))
    ages = [last_age + y for y in range(1, years + 1)]
    age = ages[-1]

    wg = sum(w * r["g"] for w, r in seasons)
    mpg = (wmin + MPG_PRIOR[0] * MPG_PRIOR[1]) / (wg + MPG_PRIOR[1])
    mpg = max(0.0, min(40.0, mpg + sum(age_mpg_delta(a) for a in ages)))

    # Availability only from completed seasons, where a full season is `season_games`.
    done = [(w, r) for w, r in seasons if int(r["season"]) < target_season]
    missed_w = [w * MISSED_SEASON_WEIGHT for w in missed]
    if done:
        avail = sum(w * min(r["g"], season_games) for w, r in done) / (
            sum(w * season_games for w, _ in done) + sum(missed_w) * season_games)
    else:
        avail = GP_PRIOR_RATE
    avail = 0.75 * avail + 0.25 * GP_PRIOR_RATE
    gp = round(games_remaining * avail)

    mult = 1.0
    for a in ages:
        mult *= age_rate_mult(a)
    proj = {c: rates[c] * mpg * mult for c in COUNTING}
    proj.update(age=age, mpg=mpg, gp=gp)
    return proj


def load_overrides(path: Path | None = None) -> list[dict]:
    path = path or OVERRIDES_PATH
    if not path.exists():
        return []
    with path.open() as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def apply_override(p: dict, o: dict) -> None:
    if o.get("team"):
        p["team"] = norm_team(o["team"])
    if o.get("pos"):
        p["pos"] = o["pos"].replace("/", ",").upper()
    mpg = _num(o.get("mpg"))
    if mpg is not None and p.get("mpg"):
        scale = mpg / p["mpg"]
        for c in COUNTING:
            p[c] *= scale
        p["mpg"] = mpg
    elif mpg is not None:
        p["mpg"] = mpg
    usage = _num(o.get("usage_mult"))
    if usage is not None:
        for c in COUNTING:
            p[c] *= usage
    for c in COUNTING:
        v = _num(o.get(c))
        if v is not None:
            p[c] = v
    gp = _num(o.get("gp"))
    if gp is not None:
        p["gp"] = gp
    out = _num(o.get("games_out"))
    if out:
        p["gp"] = max(0, p.get("gp", 0) - out)
    if o.get("note"):
        p["note"] = o["note"]
    p["override"] = True


def finalize(p: dict) -> dict:
    p["treb"] = p["oreb"] + p["dreb"]
    p["fg_pct"] = p["fgm"] / p["fga"] if p["fga"] else 0.0
    p["ft_pct"] = p["ftm"] / p["fta"] if p["fta"] else 0.0
    p["fg3_pct"] = p["fg3m"] / p["fg3a"] if p["fg3a"] else 0.0
    for k, v in list(p.items()):
        if isinstance(v, float):
            p[k] = round(v, 4)
    p["gp"] = int(round(p["gp"]))
    return p


def build_projections(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_config()
    target = cfg["season"]
    seasons = list(zip(cfg["history"], cfg["history_weights"]))

    by_player: dict[str, list[tuple[float, dict]]] = {}
    meta: dict[str, dict] = {}
    lg = None
    current = load_season(target) if cfg.get("include_current_season") else []
    if current:
        # The in-progress season counts more as minutes accumulate.
        top = max(cfg["history_weights"])
        for r in current:
            r["season"] = target
            w = 1.5 * top * min(1.0, r["min"] / 1000)
            by_player.setdefault(r["bbref_id"], []).append((w, r))
            meta[r["bbref_id"]] = r
    loaded = set()
    for season, weight in seasons:
        rows = load_season(season)
        if not rows:
            continue
        loaded.add(season)
        if lg is None:
            lg = league_rates(rows)
        for r in rows:
            r["season"] = season
            by_player.setdefault(r["bbref_id"], []).append((weight, r))
            meta.setdefault(r["bbref_id"], r)
    if lg is None:
        raise SystemExit("No season data found in data/raw. Run `python -m app.cli fetch` first.")

    players = {}
    for pid, hist in by_player.items():
        hist = [(w, r) for w, r in hist if w > 0 and r["min"] > 0]
        if not hist:
            continue
        hist.sort(key=lambda wr: -int(wr[1]["season"]))
        m = meta[pid]
        played = {int(r["season"]) for _, r in hist}
        # Completed seasons after the player's first one in our window that he didn't play at all.
        missed = [(s, w) for s, w in seasons if s in loaded and s > min(played) and s not in played]
        p = project_player(hist, lg, target, cfg["season_games"], cfg["games_remaining"],
                           missed=[w for _, w in missed])
        p.update(id=pid, name=m["name"], key=norm_name(m["name"]), team=m["team"], pos=m["pos"],
                 seasons=sorted(played, reverse=True), last_season=max(played),
                 missed_seasons=[s for s, _ in missed])
        players[p["key"]] = p

    for o in load_overrides():
        key = norm_name(o.get("player", ""))
        if not key:
            continue
        if key in players:
            apply_override(players[key], o)
        else:
            # New player (rookie, overseas signing): needs mpg + stats in the overrides row.
            p = {c: 0.0 for c in COUNTING}
            p.update(id=f"manual:{key}", name=o["player"], key=key, team="", pos="",
                     age=_num(o.get("age")) or 0, mpg=0.0, gp=cfg["games_remaining"] * GP_PRIOR_RATE,
                     seasons=[], last_season=None, rookie=True)
            apply_override(p, o)
            players[key] = p

    # Drop players who stopped playing long ago (retired / out of the league).
    keep = [p for p in players.values() if p.get("override") or (p["last_season"] or 0) >= target - 2]
    out = [finalize(p) for p in keep]
    out.sort(key=lambda p: -p["pts"])
    return out


def write_projections(cfg: dict | None = None) -> Path:
    players = build_projections(cfg)
    OUT_PATH.write_text(json.dumps(players))
    print(f"Wrote {len(players)} projections to {OUT_PATH.relative_to(DATA_DIR.parent)}")
    return OUT_PATH
