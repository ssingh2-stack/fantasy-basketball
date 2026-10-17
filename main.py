"""Entry point for hosts that auto-detect Python apps (e.g. Railway's Railpack runs main.py
or `uvicorn main:app`). Locally, `python -m app.cli serve` does the same thing."""
import os

import uvicorn

from app.server import app  # noqa: F401  (exposed for `uvicorn main:app`)

if __name__ == "__main__":
    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
