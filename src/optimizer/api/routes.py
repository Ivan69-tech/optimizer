"""
Routes FastAPI — POST /optimize, GET /health, /sites/{id}/trajectory, /sites/{id}/status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from optimizer.api.schemas import (
    HealthResponse,
    OptimizeRequest,
    OptimizeResponse,
    SiteStatus,
    TrajectoryStep,
)
from optimizer.config import ConfigYaml, load_config_yaml
from optimizer.db import readers
from optimizer.db.session import get_session
from optimizer.exceptions import (
    ForecastsMissingError,
    InfeasibleProblemError,
    SiteNotFoundError,
)
from optimizer.pipeline import optimize as pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_cfg: ConfigYaml | None = None


def get_config() -> ConfigYaml:
    """Dépendance : charge config.yaml une fois."""
    global _cfg
    if _cfg is None:
        _cfg = load_config_yaml()
    return _cfg


@router.post("/optimize", response_model=OptimizeResponse)
def post_optimize(
    request: OptimizeRequest,
    session: Session = Depends(get_session),
    cfg: ConfigYaml = Depends(get_config),
) -> OptimizeResponse:
    logger.info("optimize | site=%s | soc=%.1f kWh", request.site_id, request.soc_actuel_kwh)
    try:
        resultat = pipeline.run_optimization(
            session=session,
            site_id=request.site_id,
            soc_actuel_kwh=request.soc_actuel_kwh,
            cfg=cfg,
        )
    except SiteNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    except ForecastsMissingError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err)
        ) from err
    except InfeasibleProblemError as err:
        logger.exception("Solveur en échec pour site=%s", request.site_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err)
        ) from err

    return OptimizeResponse(
        site_id=resultat.site_id,
        timestamp_calcul=resultat.timestamp_calcul,
        horizon_debut=resultat.horizon_debut,
        trajectoire=[
            TrajectoryStep(
                timestamp=p.timestamp,
                energie_kwh=p.energie_kwh,
                soc_cible_kwh=p.soc_cible_kwh,
            )
            for p in resultat.pas_reponse
        ],
        statut=resultat.statut,
        message=resultat.message,
    )


@router.get("/health", response_model=HealthResponse)
def get_health(session: Session = Depends(get_session)) -> HealthResponse:
    db_ok = False
    nb_sites = 0
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("health | DB inaccessible | %s", exc)
    if db_ok:
        try:
            from optimizer.db.models import Trajectoire
            nb_sites = session.query(Trajectoire.site_id).distinct().count()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health | lecture trajectoires impossible | %s", exc)
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        sites_avec_trajectoire=nb_sites,
    )


@router.get("/sites/{site_id}/trajectory", response_model=OptimizeResponse)
def get_trajectory(
    site_id: str,
    session: Session = Depends(get_session),
    cfg: ConfigYaml = Depends(get_config),
) -> OptimizeResponse:
    trajectoire = readers.get_derniere_trajectoire(session, site_id)
    if trajectoire is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucune trajectoire pour site_id={site_id}.",
        )
    pas = readers.get_pas_trajectoire(session, trajectoire.id)
    pas_reponse = pas[: cfg.nb_pas_reponse]
    return OptimizeResponse(
        site_id=trajectoire.site_id,
        timestamp_calcul=trajectoire.timestamp_calcul,
        horizon_debut=trajectoire.horizon_debut,
        trajectoire=[
            TrajectoryStep(
                timestamp=p.timestamp,
                energie_kwh=p.energie_kwh,
                soc_cible_kwh=p.soc_cible_kwh,
            )
            for p in pas_reponse
        ],
        statut=trajectoire.statut,
        message=trajectoire.message or "",
    )


@router.get("/sites/{site_id}/status", response_model=SiteStatus)
def get_status(
    site_id: str,
    session: Session = Depends(get_session),
) -> SiteStatus:
    trajectoire = readers.get_derniere_trajectoire(session, site_id)
    if trajectoire is None:
        return SiteStatus(site_id=site_id)
    return SiteStatus(
        site_id=site_id,
        derniere_timestamp_calcul=trajectoire.timestamp_calcul,
        derniere_statut=trajectoire.statut,
        derive_pct=trajectoire.derive_pct,
    )
