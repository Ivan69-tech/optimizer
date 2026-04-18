"""
Calcul de la dérive entre la trajectoire précédente et l'état réel.

La dérive est exprimée en pourcentage de la capacité totale de la batterie :
    derive_pct = |soc_actuel_mesure − soc_prevu_par_derniere_trajectoire| / capacite × 100

Si la dérive dépasse le seuil configuré, le pipeline marque la nouvelle
trajectoire comme "corrective".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from optimizer.db.models import Trajectoire, TrajectoirePas


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
        return None

    pas_proche = (
        session.query(TrajectoirePas)
        .filter(TrajectoirePas.trajectoire_id == trajectoire_precedente.id)
        .filter(TrajectoirePas.timestamp <= timestamp_requete)
        .order_by(TrajectoirePas.timestamp.desc())
        .first()
    )
    if pas_proche is None:
        return None

    ecart_kwh = abs(soc_actuel_kwh - pas_proche.soc_cible_kwh)
    return float(ecart_kwh / capacite_bess_kwh * 100.0)
