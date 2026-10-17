"""Usage:
  python -m app.cli fetch [--force]   download season totals from Basketball-Reference
  python -m app.cli project           build data/projections.json
  python -m app.cli sync              pull rosters/ADP from your ESPN or Yahoo league
  python -m app.cli backtest [--season 2026]
                                      score the model on a finished season
  python -m app.cli serve [--host H] [--port N]
                                      start the web app (default http://localhost:8000;
                                      HOST and PORT env vars also work, e.g. on Railway)
"""
import argparse
import os

from .config import load_config


def main():
    ap = argparse.ArgumentParser(description="Fantasy basketball projections")
    ap.add_argument("command", choices=["fetch", "project", "sync", "serve", "backtest"])
    ap.add_argument("--force", action="store_true", help="re-download seasons already saved")
    ap.add_argument("--season", type=int, help="season to backtest (default: last completed)")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = ap.parse_args()
    cfg = load_config()

    if args.command == "fetch":
        from .scrape import fetch_all
        fetch_all(cfg, force=args.force)
    elif args.command == "project":
        from .project import write_projections
        write_projections(cfg)
    elif args.command == "sync":
        from .server import sync_league
        print(sync_league())
    elif args.command == "backtest":
        from .backtest import run
        print(run(args.season))
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("app.server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
