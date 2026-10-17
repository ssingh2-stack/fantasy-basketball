"""Usage:
  python -m app.cli fetch [--force]   download season totals from Basketball-Reference
  python -m app.cli project           build data/projections.json
  python -m app.cli sync              pull rosters/ADP from your ESPN or Yahoo league
  python -m app.cli serve [--port N]  start the web app (default http://localhost:8000)
"""
import argparse

from .config import load_config


def main():
    ap = argparse.ArgumentParser(description="Fantasy basketball projections")
    ap.add_argument("command", choices=["fetch", "project", "sync", "serve"])
    ap.add_argument("--force", action="store_true", help="re-download seasons already saved")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    cfg = load_config()

    if args.command == "fetch":
        from .scrape import fetch_seasons
        if cfg.get("include_current_season"):
            # The in-progress season changes daily, so always refresh it.
            fetch_seasons([cfg["season"]], force=True)
        fetch_seasons(cfg["history"], force=args.force)
    elif args.command == "project":
        from .project import write_projections
        write_projections(cfg)
    elif args.command == "sync":
        from .server import sync_league
        print(sync_league())
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("app.server:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
