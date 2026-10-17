"""Turn past seasons into per-game projections for the upcoming season.

Model (similar in spirit to Marcel-style baseball projections):
  1. Per-minute rates for volume stats (shot attempts, rebounds, assists, steals...),
     weighting recent seasons more (and the in-progress season by minutes played).
  2. Regress each rate toward league average by its own amount: noisy stats like steals
     get more regression than stable ones like rebounds. Shooting percentages are
     regressed separately, on attempts, so a hot 3-point season on low volume is tempered.
  3. Apply an aging curve to the rates and to minutes.
  4. Project minutes per game and games played (from each player's availability history,
     counting fully missed seasons).
  5. Put players on their current teams (from Basketball-Reference rosters) and apply
     manual overrides from data/overrides.csv (new roles, injuries, rookies).
  6. Fit each team's projected minutes into the 240 available per game.
"""
import csv
import json
from pathlib import Path

from .config import DATA_DIR, load_config
from .names import fix_mojibake, norm_name, norm_team
from .scrape import RAW_DIR, load_rosters

COUNTING = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pts"]
NUMERIC = ["age", "g", "gs", "min"] + COUNTING
# Minutes of league-average play mixed into each per-minute rate. Larger = noisier stat.
VOLUME_REGRESSION = {"fga": 200, "fg3a": 200, "fta": 250, "oreb": 150, "dreb": 150,
                     "ast": 150, "stl": 350, "blk": 200, "tov": 250}
# League-average attempts mixed into each shooting percentage. 3P% is the noisiest.
PCT_REGRESSION = {"fg2": 250, "fg3": 600, "ft": 150}
TEAM_MINUTES = 240  # minutes per team per game (5 players x 48)
MIN_MPG_SHARE = 0.6  # the minutes budget never cuts a player below 60% of his projection
MPG_PRIOR = (20.0, 5)  # regress MPG toward 20 with the weight of 5 games
# Minutes follow a player's current role, so they lean much harder on last season than the
# per-minute stats do (backtest: 3-season-weighted minutes lost to "repeat last season" and
# missed young players whose roles grew). Weights by recency of completed seasons played.
MPG_RECENCY = [1.0, 0.15, 0.05]
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
    """Per-minute rates and shooting percentages of rotation players."""
    pool = [r for r in rows if r["min"] >= min_minutes] or rows
    s = {c: sum(r[c] for r in pool) for c in COUNTING + ["min"]}
    lg = {c: s[c] / (s["min"] or 1.0) for c in COUNTING}
    lg["fg2_pct"] = (s["fgm"] - s["fg3m"]) / ((s["fga"] - s["fg3a"]) or 1.0)
    lg["fg3_pct"] = s["fg3m"] / (s["fg3a"] or 1.0)
    lg["ft_pct"] = s["ftm"] / (s["fta"] or 1.0)
    return lg


def regressed_rates(seasons: list[tuple[float, dict]], lg: dict) -> dict:
    """Per-minute rates for every counting stat, built from regressed volume and percentages
    so that made shots and points stay consistent with attempts."""
    wsum = lambda f: sum(w * f(r) for w, r in seasons)  # noqa: E731
    wmin = wsum(lambda r: r["min"])
    rates = {c: (wsum(lambda r, c=c: r[c]) + k * lg[c]) / (wmin + k) for c, k in VOLUME_REGRESSION.items()}

    def pct(made, att, prior, k):
        return (wsum(made) + k * prior) / (wsum(att) + k)

    fg2 = pct(lambda r: r["fgm"] - r["fg3m"], lambda r: r["fga"] - r["fg3a"], lg["fg2_pct"], PCT_REGRESSION["fg2"])
    fg3 = pct(lambda r: r["fg3m"], lambda r: r["fg3a"], lg["fg3_pct"], PCT_REGRESSION["fg3"])
    ft = pct(lambda r: r["ftm"], lambda r: r["fta"], lg["ft_pct"], PCT_REGRESSION["ft"])

    rates["fg3a"] = min(rates["fg3a"], rates["fga"])
    rates["fg3m"] = rates["fg3a"] * fg3
    rates["fgm"] = (rates["fga"] - rates["fg3a"]) * fg2 + rates["fg3m"]
    rates["ftm"] = rates["fta"] * ft
    rates["pts"] = 2 * rates["fgm"] + rates["fg3m"] + rates["ftm"]
    return rates


def project_player(seasons: list[tuple[float, dict]], lg: dict, target_season: int,
                   season_games: int, games_remaining: int, missed: list[float] = ()) -> dict:
    """seasons: list of (weight, row) newest first; missed: weights of completed seasons
    the player sat out entirely. Returns a per-game projection."""
    rates = regressed_rates(seasons, lg)

    latest_w, latest = seasons[0]
    # Age every year since the player last played, not just one (matters after a missed season).
    last_age = latest["age"] or 26
    years = max(1, target_season - int(latest["season"]))
    ages = [last_age + y for y in range(1, years + 1)]
    age = ages[-1]

    if MPG_RECENCY is None:
        mpg_seasons = seasons
    else:
        completed = [r for _, r in seasons if int(r["season"]) < target_season]
        mpg_seasons = [(MPG_RECENCY[i] if i < len(MPG_RECENCY) else 0.0, r) for i, r in enumerate(completed)]
        # The in-progress season keeps its own (minutes-based) weight.
        mpg_seasons += [(w, r) for w, r in seasons if int(r["season"]) == target_season]
    mw_min = sum(w * r["min"] for w, r in mpg_seasons)
    mw_g = sum(w * r["g"] for w, r in mpg_seasons)
    mpg = (mw_min + MPG_PRIOR[0] * MPG_PRIOR[1]) / (mw_g + MPG_PRIOR[1])
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
    if mpg is not None:
        p["mpg_locked"] = True
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


def apply_team_minutes(players: list[dict], games: int) -> None:
    """Fit each team's projected minutes into TEAM_MINUTES per game.

    A team's expected minutes per game is the sum of mpg x share of games played. When that
    exceeds 240 (common after an offseason of additions), the excess is taken mostly from
    low-minute players, so a new star squeezes the bench more than the other starters.
    Players whose minutes were set by hand in overrides.csv are left alone.
    """
    teams: dict[str, list[dict]] = {}
    for p in players:
        if p.get("team") and p["team"] != "FA" and p.get("gp"):
            teams.setdefault(p["team"], []).append(p)
    for roster in teams.values():
        share = lambda p: min(1.0, p["gp"] / games)  # noqa: E731
        total = sum(p["mpg"] * share(p) for p in roster)
        excess = total - TEAM_MINUTES
        flexible = [p for p in roster if not p.get("mpg_locked")]
        if excess <= 0 or not flexible:
            continue
        # Weight cuts toward bench players: a 12-mpg reserve is far more flexible than a 36-mpg star.
        flex = {id(p): p["mpg"] * share(p) * max(0.05, 1 - p["mpg"] / 42) for p in flexible}
        total_flex = sum(flex.values())
        for p in flexible:
            cut = excess * flex[id(p)] / total_flex / share(p)
            new_mpg = max(p["mpg"] * MIN_MPG_SHARE, p["mpg"] - cut)
            scale = new_mpg / p["mpg"]
            for c in COUNTING:
                p[c] *= scale
            p["mpg_budget_cut"] = round(p["mpg"] - new_mpg, 1)
            p["mpg"] = new_mpg


def late_season_risk(p: dict, tank_teams: set[str]) -> dict:
    """How likely a player is to be on the floor during H2H fantasy playoffs (late March to
    early April) relative to the rest of the season. factor < 1 = risk, > 1 = late boost."""
    factor, reasons = 1.0, []
    age = p.get("age") or 0
    if p.get("team") in tank_teams:
        if age >= 29:
            factor *= 0.8
            reasons.append("Veteran on a likely lottery team: often shut down or traded by March")
        elif p.get("rookie") or 0 < age <= 24:
            factor *= 1.05
            reasons.append("Young player on a likely lottery team: late-season minutes tend to grow")
    if age >= 35:
        factor *= 0.85
        reasons.append("Age 35+: rest days are likely late in the season")
    # General injury risk is deliberately not counted here: it's already in projected games
    # played and the GP penalty. This only covers late-season shutdowns and rest.
    return {"factor": round(max(0.6, factor), 2), "reasons": reasons}


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


def build_projections(cfg: dict | None = None, use_overrides: bool = True) -> list[dict]:
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

    # Current rosters reflect every offseason trade and signing. Only trust them when all
    # 30 teams were fetched; otherwise fall back to each player's last team.
    rosters = load_rosters(target)
    free_agents = set()
    if rosters and len(set(rosters.values())) >= 30:
        for p in players.values():
            team = rosters.get(p["id"])
            if team is None:
                free_agents.add(p["key"])
            elif team != p["team"]:
                p["prev_team"], p["team"] = p["team"], team

    for o in (load_overrides() if use_overrides else []):
        key = norm_name(o.get("player", ""))
        if not key:
            continue
        if key in players:
            apply_override(players[key], o)
        elif not (_num(o.get("mpg")) and _num(o.get("pts"))):
            # No stat line to build from: most likely a name that doesn't match Basketball-Reference.
            print(f"  override for unknown player {o['player']!r} ignored (check the spelling)")
        else:
            # New player (rookie, overseas signing): needs mpg + stats in the overrides row.
            p = {c: 0.0 for c in COUNTING}
            p.update(id=f"manual:{key}", name=o["player"], key=key, team="", pos="",
                     age=_num(o.get("age")) or 0, mpg=0.0, gp=cfg["games_remaining"] * GP_PRIOR_RATE,
                     seasons=[], last_season=None, rookie=True)
            apply_override(p, o)
            players[key] = p

    # Drop unsigned free agents and players who stopped playing long ago, unless overridden.
    keep = [p for p in players.values()
            if p.get("override") or ((p["last_season"] or 0) >= target - 2 and p["key"] not in free_agents)]
    if cfg.get("team_minutes_budget"):
        apply_team_minutes(keep, cfg["games_remaining"])
    tank_teams = set(cfg.get("tank_teams", []))
    for p in keep:
        p["late_risk"] = late_season_risk(p, tank_teams)
    out = [finalize(p) for p in keep]
    out.sort(key=lambda p: -p["pts"])
    return out


def write_projections(cfg: dict | None = None) -> Path:
    players = build_projections(cfg)
    OUT_PATH.write_text(json.dumps(players))
    print(f"Wrote {len(players)} projections to {OUT_PATH.relative_to(DATA_DIR.parent)}")
    return OUT_PATH
