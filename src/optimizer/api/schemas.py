"""
Schémas Pydantic pour l'API REST.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OptimizeRequest(BaseModel):
    """Payload du POST /api/v1/optimize."""

    site_id: str = Field(..., min_length=1, max_length=64)
    soc_actuel_kwh: float = Field(..., ge=0)
    capacite_bess_kwh: float = Field(..., gt=0)


class TrajectoryStep(BaseModel):
    """Un pas de la trajectoire retournée."""

    timestamp: datetime
    energie_kwh: float  # convention producteur : positif = décharge
    soc_cible_kwh: float


class OptimizeResponse(BaseModel):
    """Réponse du POST /api/v1/optimize."""

    site_id: str
    timestamp_calcul: datetime
    horizon_debut: datetime
    trajectoire: list[TrajectoryStep]
    statut: str
    message: str = ""


class SiteStatus(BaseModel):
    """Réponse du GET /api/v1/sites/{site_id}/status."""

    site_id: str
    derniere_timestamp_calcul: datetime | None = None
    derniere_statut: str | None = None
    derive_pct: float | None = None


class HealthResponse(BaseModel):
    """Réponse du GET /api/v1/health."""

    status: str
    database: bool
    sites_avec_trajectoire: int
