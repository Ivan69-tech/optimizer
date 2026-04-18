"""
Point d'entrée de l'application FastAPI.

Usage dev :
    uv run uvicorn optimizer.main:app --host 0.0.0.0 --port 8080 --reload

Usage prod (conteneur) :
    uvicorn optimizer.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from optimizer.api.routes import router
from optimizer.config import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def create_app() -> FastAPI:
    _setup_logging()
    application = FastAPI(
        title="Service d'Optimisation BESS",
        description="L2 — Trajectoire énergétique optimale d'une batterie (Tewa Solar SGE).",
        version="0.1.0",
    )
    application.include_router(router)
    return application


app = create_app()


def main() -> None:
    """Point d'entrée console (setuptools) — lance uvicorn."""
    import uvicorn

    uvicorn.run("optimizer.main:app", host="0.0.0.0", port=8080, reload=False)
