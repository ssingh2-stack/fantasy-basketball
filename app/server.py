import html
import json
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import DATA_DIR, ROOT, load_config
from .leagues import espn, yahoo
from .project import OUT_PATH, write_projections
from .scrape import fetch_all, load_schedule

LEAGUE_PATH = DATA_DIR / "league.json"
# Hours between automatic data refreshes (in-season stats change daily); 0 disables.
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", 24))

build_lock = threading.Lock()
status = {"state": "idle", "message": "", "updated_at": None}


def refresh_data() -> None:
    """Download stats and rebuild projections, recording progress in `status`."""
    with build_lock:
        status.update(state="building", message="Downloading stats from Basketball-Reference")
        try:
            cfg = load_config()
            fetch_all(cfg)
            status["message"] = "Building projections"
            write_projections(cfg)
            status.update(state="ready", message="", updated_at=datetime.now(timezone.utc).isoformat())
        except BaseException as e:  # SystemExit from the model when no data was downloaded
            traceback.print_exc()
            status.update(state="error", message=f"Data refresh failed: {e}")


def refresh_loop() -> None:
    if not OUT_PATH.exists():
        refresh_data()
    while REFRESH_HOURS > 0:
        time.sleep(REFRESH_HOURS * 3600)
        refresh_data()


@asynccontextmanager
async def lifespan(_app):
    # Hosts like Railway start with an empty disk, so build the data in the background.
    threading.Thread(target=refresh_loop, daemon=True).start()
    yield


app = FastAPI(title="Fantasy Hoops", lifespan=lifespan)
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
        if status["state"] == "error":
            raise HTTPException(500, status["message"])
        raise HTTPException(503, (status["message"] or "Preparing data") + ". This takes about a minute on first start.")
    projections = json.loads(OUT_PATH.read_text())
    league = json.loads(LEAGUE_PATH.read_text()) if LEAGUE_PATH.exists() else None
    out = merge(projections, league)
    cfg = load_config()
    out["config"] = {"season": cfg["season"], "platform": cfg["league"]["platform"],
                     "games_remaining": cfg["games_remaining"],
                     "yahoo_connected": yahoo.is_connected(),
                     "yahoo_configured": bool(cfg["yahoo"].get("client_id")),
                     "league_configured": league_configured(cfg) or league is not None,
                     "data_updated_at": status["updated_at"]}
    out["schedule"] = load_schedule(cfg["season"])
    return out


def league_configured(cfg: dict) -> bool:
    if cfg["league"]["platform"] == "yahoo":
        return bool(cfg["yahoo"].get("league_id"))
    return bool(cfg["league"].get("league_id"))


backtest_job = {"state": "idle", "output": "", "started": 0.0}


def run_backtest(season: int | None) -> None:
    from .backtest import run
    try:
        with build_lock:  # wait for any data refresh, so we never read half-written files
            backtest_job["state"] = "running"
            backtest_job["output"] = run(season)
        backtest_job["state"] = "done"
    except BaseException as e:
        traceback.print_exc()
        backtest_job.update(state="error", output=f"Backtest failed: {e}")


@app.get("/backtest", response_class=HTMLResponse)
def backtest(season: int | None = None, rerun: bool = False):
    """Model accuracy on the last finished season. Runs in the background (it can take a few
    minutes right after a deploy, while stats download); this page reloads until it's done."""
    job = backtest_job
    if job["state"] in ("idle", "error") or (rerun and job["state"] == "done"):
        job.update(state="queued", output="", started=time.time())
        threading.Thread(target=run_backtest, args=(season,), daemon=True).start()
    if job["state"] == "done":
        return f"""<!doctype html><title>Backtest</title><meta name="viewport" content="width=device-width">
<body style="background:#12151f;color:#e8ebf3;font-family:system-ui;padding:16px">
<p><a style="color:#f08a24" href="/backtest?rerun=true">Run again</a> · <a style="color:#f08a24" href="/">Rankings</a></p>
<pre style="white-space:pre-wrap;font-size:13px">{html.escape(job["output"])}</pre></body>"""
    if job["state"] == "error":
        body = html.escape(job["output"])
        refresh = ""
    else:
        waiting = job["state"] == "queued" and status["state"] == "building"
        body = ("Waiting for the stats download to finish: " + html.escape(status["message"] or "")
                if waiting else "Running the backtest (downloading one extra season, then six model runs)")
        body += f"... {int(time.time() - job['started'])}s so far. This page reloads by itself."
        refresh = '<meta http-equiv="refresh" content="5">'
    return f"""<!doctype html><title>Backtest</title>{refresh}<meta name="viewport" content="width=device-width">
<body style="background:#12151f;color:#e8ebf3;font-family:system-ui;padding:16px"><p>{body}</p></body>"""


@app.post("/api/project")
def rebuild():
    """Re-run the model (e.g. after editing data/overrides.csv)."""
    with build_lock:
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
