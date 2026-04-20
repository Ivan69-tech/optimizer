"""
Point d'entrée de l'application FastAPI.

Usage dev :
    uv run uvicorn optimizer.main:app --host 0.0.0.0 --port 8080 --reload

Usage prod (conteneur) :
    uvicorn optimizer.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

from optimizer.api.routes import router
from optimizer.config import settings

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def create_app() -> FastAPI:
    _setup_logging()

    logger.info(
        "Démarrage optimizer-service | log_level=%s | config=%s",
        settings.log_level.upper(),
        settings.config_path,
    )
    try:
        from optimizer.db.session import engine

        with engine.connect():
            pass
        logger.info("DB connectée")
    except Exception as exc:
        logger.warning("DB inaccessible | %s", exc)

    application = FastAPI(
        title="Service d'Optimisation BESS",
        description="L2 — Trajectoire énergétique optimale d'une batterie",
        version="0.1.0",
    )

    @application.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "%s %s | status=%d | %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    application.include_router(router)
    logger.info("Service prêt")
    return application


app = create_app()


def main() -> None:
    """Point d'entrée console (setuptools) — lance uvicorn."""
    import uvicorn

    uvicorn.run("optimizer.main:app", host="0.0.0.0", port=8080, reload=False)
