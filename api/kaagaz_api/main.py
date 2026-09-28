"""Kaagaz-OCR API: the front door the UI talks to.

Day 1 adds the auth, uploads and documents routers and the storage and Temporal
clients.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Kaagaz-OCR API", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Day 1: also check the database, storage and Temporal, and report each one.
        return {"status": "ok"}

    # Routers go above this line: the UI mount catches every path not matched before it.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


app = create_app()
