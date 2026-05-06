"""
Calcul de la dérive entre la trajectoire précédente et l'état réel.

La dérive est exprimée en pourcentage de la capacité totale de la batterie :
    derive_pct = |soe_actuel_mesure − soe_prevu_par_derniere_trajectoire| / capacite × 100

Le SoE prévu est calculé par interpolation linéaire entre les deux pas encadrant
`timestamp_requete`. Si la dérive dépasse le seuil configuré, le pipeline marque
la nouvelle trajectoire comme "corrective".
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from optimizer.db.models import Trajectoire, TrajectoirePas

logger = logging.getLogger(__name__)


def _strip_tz(ts: datetime) -> datetime:
    """Retire tzinfo pour une arithmétique homogène SQLite/PostgreSQL."""
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def calcul_derive_pct(
    session: Session,
    trajectoire_precedente: Trajectoire | None,
    soe_actuel_kwh: float,
    timestamp_requete: datetime,
    capacite_bess_kwh: float,
) -> float | None:
    """
    Retourne la dérive en % de la capacité, ou None s'il n'y a pas de
    trajectoire précédente exploitable.

    Le SoE prévu est interpolé linéairement entre le pas immédiatement avant
    et le pas immédiatement après `timestamp_requete`. Si aucun pas après
    n'existe (fin de trajectoire), on utilise le dernier pas connu.
    """
    if trajectoire_precedente is None or capacite_bess_kwh <= 0:
        logger.debug("drift | pas de trajectoire précédente — dérive non calculée")
        return None

    site_id = trajectoire_precedente.site_id

    pas_avant = (
        session.query(TrajectoirePas)
        .filter(TrajectoirePas.site_id == site_id)
        .filter(TrajectoirePas.timestamp <= timestamp_requete)
        .order_by(TrajectoirePas.timestamp.desc())
        .first()
    )
    if pas_avant is None:
        logger.debug("drift | site=%s | aucun pas antérieur trouvé", site_id)
        return None

    pas_apres = (
        session.query(TrajectoirePas)
        .filter(TrajectoirePas.site_id == site_id)
        .filter(TrajectoirePas.timestamp > timestamp_requete)
        .order_by(TrajectoirePas.timestamp.asc())
        .first()
    )

    if pas_apres is not None:
        t_avant = _strip_tz(pas_avant.timestamp)
        t_apres = _strip_tz(pas_apres.timestamp)
        t_now = _strip_tz(timestamp_requete)
        alpha = (t_now - t_avant).total_seconds() / (t_apres - t_avant).total_seconds()
        soe_attendu = pas_avant.soe_cible_kwh + alpha * (
            pas_apres.soe_cible_kwh - pas_avant.soe_cible_kwh
        )
    else:
        soe_attendu = pas_avant.soe_cible_kwh

    derive = float(abs(soe_actuel_kwh - soe_attendu) / capacite_bess_kwh * 100.0)
    logger.info("drift | site=%s | derive=%.1f%%", site_id, derive)
    return derive
