"""
Écritures centralisées sur PostgreSQL.

Seules tables écrites : `trajectoires_optimisees` et `trajectoire_pas`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from optimizer.db.models import Trajectoire, TrajectoirePas

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PasTrajectoireNouveau:
    """Un pas de trajectoire à insérer."""

    timestamp: datetime
    energie_kwh: float
    soe_cible_kwh: float


def save_trajectoire(
    session: Session,
    site_id: str,
    timestamp_calcul: datetime,
    soe_initial_kwh: float,
    statut: str,
    message: str | None,
    derive_pct: float | None,
    horizon_debut: datetime,
    horizon_fin: datetime,
    pas: list[PasTrajectoireNouveau],
) -> Trajectoire:
    """
    Insère atomiquement une trajectoire et tous ses pas.

    La session est `flush()`-ée pour attribuer l'id auto-incrémenté, mais pas
    commit-ée — le commit reste à la charge de l'appelant (géré par
    `get_session()` en mode dépendance FastAPI).
    """
    trajectoire = Trajectoire(
        site_id=site_id,
        timestamp_calcul=timestamp_calcul,
        soe_initial_kwh=soe_initial_kwh,
        statut=statut,
        message=message,
        derive_pct=derive_pct,
        horizon_debut=horizon_debut,
        horizon_fin=horizon_fin,
        pas=[
            TrajectoirePas(
                timestamp=p.timestamp,
                energie_kwh=p.energie_kwh,
                soe_cible_kwh=p.soe_cible_kwh,
            )
            for p in pas
        ],
    )
    session.add(trajectoire)
    session.flush()

    logger.info(
        "save_trajectoire | site=%s | statut=%s | %d pas | horizon=[%s, %s]",
        site_id,
        statut,
        len(pas),
        horizon_debut.isoformat(),
        horizon_fin.isoformat(),
    )
    return trajectoire
