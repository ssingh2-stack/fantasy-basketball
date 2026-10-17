"""Backtest: project a finished season from the three before it, then compare to what happened.

    python -m app.cli backtest [--season 2026]     (or open /backtest on the web app)

Overrides, rosters and the in-progress season are left out, so this measures the model alone.
The baseline is the naive projection "same per-game stats as last season".
"""
import math
from contextlib import contextmanager

from . import project
from .config import load_config
from .project import build_projections, load_season
from .scrape import fetch_seasons


# Variants for "is each layer helping?": (label, module patches, config overrides).
VARIANTS = [
    ("without aging curve", {"age_rate_mult": lambda a: 1.0, "age_mpg_delta": lambda a: 0.0}, {}),
    ("without volume regression", {"VOLUME_REGRESSION": {k: 1e-6 for k in project.VOLUME_REGRESSION}}, {}),
    ("without percentage regression", {"PCT_REGRESSION": {k: 1e-6 for k in project.PCT_REGRESSION}}, {}),
    ("with 3-season minutes (old)", {"MPG_RECENCY": None}, {}),
    ("with last-season-only minutes", {"MPG_RECENCY": [1.0]}, {}),
    ("with team minutes budget", {}, {"team_minutes_budget": True}),
    ("without missed-season penalty", {"MISSED_SEASON_WEIGHT": 0.0}, {}),
]


@contextmanager
def patched(patches: dict):
    """Temporarily replace model settings to measure whether a layer helps."""
    saved = {name: getattr(project, name) for name in patches}
    for name, value in patches.items():
        setattr(project, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(project, name, value)


STATS = ["pts", "treb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct", "mpg"]
NINE_CAT = ["fg_pct", "ft_pct", "fg3m", "pts", "treb", "ast", "stl", "blk", "tov"]
POOL = 156  # 12 teams x 13 roster spots
MIN_GAMES = 20  # players need this many games to be scored on per-game accuracy


def per_game(row: dict) -> dict:
    g = row["g"] or 1
    out = {k: row[k] / g for k in ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pts"]}
    out["treb"] = out["oreb"] + out["dreb"]
    out["mpg"] = row["min"] / g
    out["fg_pct"] = row["fgm"] / row["fga"] if row["fga"] else 0.0
    out["ft_pct"] = row["ftm"] / row["fta"] if row["fta"] else 0.0
    return out


def nine_cat_values(players: dict[str, dict]) -> dict[str, float]:
    """Per-game 9-cat z-score totals (the same method as the web app's Averages mode)."""
    ids = sorted(players, key=lambda i: -players[i]["mpg"])[:POOL]
    values = {}
    for _ in range(4):
        pool = [players[i] for i in ids]
        fg = sum(p["fgm"] for p in pool) / (sum(p["fga"] for p in pool) or 1)
        ft = sum(p["ftm"] for p in pool) / (sum(p["fta"] for p in pool) or 1)

        def raw(p, c):
            if c == "fg_pct":
                return p["fgm"] - fg * p["fga"]
            if c == "ft_pct":
                return p["ftm"] - ft * p["fta"]
            return p[c]
        stats = {}
        for c in NINE_CAT:
            vals = [raw(p, c) for p in pool]
            mean = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1
            stats[c] = (mean, sd)
        values = {i: sum((-1 if c == "tov" else 1) * (raw(p, c) - stats[c][0]) / stats[c][1] for c in NINE_CAT)
                  for i, p in players.items()}
        ids = sorted(values, key=lambda i: -values[i])[:POOL]
    return values


def corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else float("nan")


def ranks(values: dict[str, float]) -> dict[str, int]:
    return {i: r for r, i in enumerate(sorted(values, key=lambda i: -values[i]), 1)}


def run(season: int | None = None, fetch: bool = True) -> str:
    cfg = load_config()
    season = season or cfg["history"][0]
    history = [season - 1, season - 2, season - 3]
    if fetch:
        fetch_seasons(history + [season])
    bt_cfg = {**cfg, "season": season, "history": history, "include_current_season": False,
              "games_remaining": cfg["season_games"]}
    projected = {p["id"]: p for p in build_projections(bt_cfg, use_overrides=False)}
    actual_rows = {r["bbref_id"]: r for r in load_season(season)}
    prev_rows = {r["bbref_id"]: r for r in load_season(season - 1)}
    if not actual_rows or not projected:
        return f"Missing data for {season}: run `python -m app.cli fetch` first."

    actual = {i: per_game(r) for i, r in actual_rows.items() if r["g"] >= MIN_GAMES}
    naive = {i: per_game(r) for i, r in prev_rows.items() if r["g"] >= 10}
    sample = [i for i in actual if i in projected and i in naive]  # same players for both methods

    label = f"{season - 1}-{str(season)[2:]}"
    lines = [f"Backtest: projecting {label} from {history[-1] - 1}-{str(history[0])[2:]} "
             f"(model only: no overrides, rosters, or in-season data)",
             f"Players scored: {len(sample)} (played {MIN_GAMES}+ games in {label} and 10+ the year before)", "",
             "Per-game accuracy (lower MAE / higher r is better; baseline = repeat last season)",
             f"{'stat':8} {'model MAE':>10} {'base MAE':>10} {'model r':>8} {'base r':>8}  verdict"]
    for s in STATS:
        a = [actual[i][s] for i in sample]
        m = [projected[i][s] for i in sample]
        b = [naive[i][s] for i in sample]
        if s in ("fg_pct", "ft_pct"):  # percentages only count for players who shoot
            att = "fga" if s == "fg_pct" else "fta"
            keep = [k for k, i in enumerate(sample) if actual[i][att] >= 2]
            a, m, b = ([v[k] for k in keep] for v in (a, m, b))
        mae_m = sum(abs(x - y) for x, y in zip(m, a)) / len(a)
        mae_b = sum(abs(x - y) for x, y in zip(b, a)) / len(a)
        verdict = "better" if mae_m < mae_b * 0.98 else "worse" if mae_m > mae_b * 1.02 else "same"
        fmt = "{:>10.3f}" if s.endswith("pct") else "{:>10.2f}"
        lines.append(f"{s:8} {fmt.format(mae_m)} {fmt.format(mae_b)} {corr(m, a):>8.3f} {corr(b, a):>8.3f}  {verdict}")

    # Games played: everyone who played real minutes last season; missing all season counts as 0.
    gp_ids = [i for i, r in prev_rows.items() if r["min"] >= 500 and i in projected]
    gp_actual = [actual_rows[i]["g"] if i in actual_rows else 0 for i in gp_ids]
    gp_model = [projected[i]["gp"] for i in gp_ids]
    gp_base = [prev_rows[i]["g"] for i in gp_ids]
    lines += ["", f"Games played ({len(gp_ids)} players with 500+ min last season; a missed season counts as 0)",
              f"  model MAE {sum(abs(x - y) for x, y in zip(gp_model, gp_actual)) / len(gp_ids):.1f} games, "
              f"baseline MAE {sum(abs(x - y) for x, y in zip(gp_base, gp_actual)) / len(gp_ids):.1f}, "
              f"model avg {sum(gp_model) / len(gp_ids):.1f} vs actual avg {sum(gp_actual) / len(gp_ids):.1f}"]
    # The players you'd actually draft: the model's projected top 150 by 9-cat value.
    proj_rank = ranks(nine_cat_values({i: projected[i] for i in gp_ids}))
    drafted = [i for i in gp_ids if proj_rank[i] <= 150]
    d_model = sum(projected[i]["gp"] for i in drafted) / len(drafted)
    d_actual = sum(actual_rows[i]["g"] if i in actual_rows else 0 for i in drafted) / len(drafted)
    lines.append(f"  projected top 150 only: model avg {d_model:.1f} vs actual avg {d_actual:.1f} "
                 f"(calibration factor {d_actual / d_model:.2f})")

    # 9-cat rankings: does the projected order match how the season actually played out?
    act_val = nine_cat_values({i: actual[i] for i in sample})
    mod_val = nine_cat_values({i: projected[i] for i in sample})
    base_val = nine_cat_values({i: naive[i] for i in sample})
    ar, mr, br = ranks(act_val), ranks(mod_val), ranks(base_val)
    top = [i for i in ar if ar[i] <= 150]
    lines += ["", "9-cat per-game rankings vs actual (players in the actual top 150)",
              f"  rank correlation: model {corr([mr[i] for i in top], [ar[i] for i in top]):.3f}, "
              f"baseline {corr([br[i] for i in top], [ar[i] for i in top]):.3f}",
              f"  actual top-50 players the model had in its top 50: {sum(1 for i in ar if ar[i] <= 50 and mr[i] <= 50)}/50 "
              f"(baseline {sum(1 for i in ar if ar[i] <= 50 and br[i] <= 50)}/50)", "",
              "Biggest misses among the actual top 100 (projected rank -> actual rank)"]
    misses = sorted((i for i in ar if ar[i] <= 100), key=lambda i: -abs(mr[i] - ar[i]))[:15]
    for i in misses:
        lines.append(f"  {projected[i]['name']:28} {mr[i]:>4} -> {ar[i]:<4} "
                     f"({'model too low' if mr[i] > ar[i] else 'model too high'})")

    # Ablation: rerun with each judgment-call layer switched off. If removing a layer makes the
    # rankings better, that layer is hurting and should be toned down or dropped.
    def rank_corr(proj):
        vals = ranks(nine_cat_values({i: proj[i] for i in sample if i in proj}))
        keep = [i for i in top if i in vals]
        return corr([vals[i] for i in keep], [ar[i] for i in keep])
    full = rank_corr(projected)
    lines += ["", "Is each layer helping? (9-cat rank correlation for each variant; higher is better)",
              f"  {'current model':34} {full:.3f}"]
    for label, patches, cfg_over in VARIANTS:
        with patched(patches):
            alt = {p["id"]: p for p in build_projections({**bt_cfg, **cfg_over}, use_overrides=False)}
        score = rank_corr(alt)
        better = "variant better" if score > full + 0.005 else "current better" if full > score + 0.005 else "no difference"
        lines.append(f"  {label:34} {score:.3f}  -> {better}")
    return "\n".join(lines)
