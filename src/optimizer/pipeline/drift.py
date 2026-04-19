"""
Calcul de la dérive entre la trajectoire précédente et l'état réel.

La dérive est exprimée en pourcentage de la capacité totale de la batterie :
    derive_pct = |soc_actuel_mesure − soc_prevu_par_derniere_trajectoire| / capacite × 100

Si la dérive dépasse le seuil configuré, le pipeline marque la nouvelle
trajectoire comme "corrective".
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from optimizer.db.models import Trajectoire, TrajectoirePas

logger = logging.getLogger(__name__)


def calcul_derive_pct(
    session: Session,
    trajectoire_precedente: Trajectoire | None,
    soc_actuel_kwh: float,
    timestamp_requete: datetime,
    capacite_bess_kwh: float,
) -> float | None:
    """
    Retourne la dérive en % de la capacité, ou None s'il n'y a pas de
    trajectoire précédente exploitable.

    La comparaison se fait avec le pas de la trajectoire précédente dont
    le `timestamp` est le plus proche (et ≤) de `timestamp_requete`.
    """
    if trajectoire_precedente is None or capacite_bess_kwh <= 0:
        logger.debug("drift | pas de trajectoire précédente — dérive non calculée")
        return None

    pas_proche = (
        session.query(TrajectoirePas)
        .filter(TrajectoirePas.trajectoire_id == trajectoire_precedente.id)
        .filter(TrajectoirePas.timestamp <= timestamp_requete)
        .order_by(TrajectoirePas.timestamp.desc())
        .first()
    )
    if pas_proche is None:
        logger.debug("drift | site=%s | aucun pas antérieur trouvé", trajectoire_precedente.site_id)
        return None

    ecart_kwh = abs(soc_actuel_kwh - pas_proche.soc_cible_kwh)
    derive = float(ecart_kwh / capacite_bess_kwh * 100.0)
    logger.info("drift | site=%s | derive=%.1f%%", trajectoire_precedente.site_id, derive)
    return derive
