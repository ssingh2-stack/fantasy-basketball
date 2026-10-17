import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import DATA_DIR, ROOT, load_config
from .leagues import espn, yahoo
from .project import OUT_PATH, write_projections

LEAGUE_PATH = DATA_DIR / "league.json"

app = FastAPI(title="Fantasy Hoops")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


def merge(projections: list[dict], league: dict | None) -> dict:
    """Attach league info (owner, ADP, positions, current NBA team, injury) to each projection."""
    if not league:
        return {"players": projections, "league": None, "unmatched": []}
    by_key = {p["key"]: p for p in league["players"]}
    seen = set()
    for p in projections:
        lp = by_key.get(p["key"])
        if not lp:
            continue
        seen.add(p["key"])
        p["owner"] = lp.get("owner")
        p["adp"] = lp.get("adp")
        p["injury"] = lp.get("injury")
        if lp.get("pos"):
            p["pos"] = lp["pos"]
        # The league knows about offseason moves; a manual override still wins.
        if lp.get("team") and lp["team"] not in ("FA", p["team"]) and not p.get("override"):
            p["prev_team"] = p["team"]
            p["team"] = lp["team"]
    # Players the league cares about but we couldn't project (usually rookies).
    unmatched = sorted(
        (lp for lp in league["players"] if lp["key"] not in seen and (lp.get("owner") or lp.get("adp"))),
        key=lambda lp: lp.get("adp") or 999,
    )[:40]
    meta = {k: league[k] for k in ("platform", "league_name", "synced_at", "teams", "draft")}
    return {"players": projections, "league": meta,
            "unmatched": [{"name": u["name"], "team": u.get("team"), "adp": u.get("adp")} for u in unmatched]}


@app.get("/api/data")
def data():
    if not OUT_PATH.exists():
        raise HTTPException(404, "No projections yet. Run `python -m app.cli fetch` then `python -m app.cli project`.")
    projections = json.loads(OUT_PATH.read_text())
    league = json.loads(LEAGUE_PATH.read_text()) if LEAGUE_PATH.exists() else None
    out = merge(projections, league)
    cfg = load_config()
    out["config"] = {"season": cfg["season"], "platform": cfg["league"]["platform"],
                     "games_remaining": cfg["games_remaining"],
                     "yahoo_connected": yahoo.is_connected(),
                     "yahoo_configured": bool(cfg["yahoo"].get("client_id"))}
    return out


@app.post("/api/project")
def rebuild():
    """Re-run the model (e.g. after editing data/overrides.csv)."""
    write_projections()
    return {"ok": True}


@app.post("/api/league/sync")
def sync_league():
    cfg = load_config()
    platform = cfg["league"]["platform"]
    try:
        league = (yahoo if platform == "yahoo" else espn).sync(cfg)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # network errors, API changes
        raise HTTPException(502, f"{platform} sync failed: {e}")
    LEAGUE_PATH.write_text(json.dumps(league))
    return {"ok": True, "players": len(league["players"]), "teams": len(league["teams"])}


@app.get("/api/yahoo/auth-url")
def yahoo_auth_url():
    cfg = load_config()
    if not cfg["yahoo"].get("client_id"):
        raise HTTPException(400, "Set yahoo.client_id and yahoo.client_secret in config.json")
    return {"url": yahoo.auth_url(cfg)}


class Code(BaseModel):
    code: str


@app.post("/api/yahoo/code")
def yahoo_code(body: Code):
    try:
        yahoo.exchange_code(load_config(), body.code)
    except Exception as e:
        raise HTTPException(400, f"Yahoo rejected the code: {e}")
    return {"ok": True}
