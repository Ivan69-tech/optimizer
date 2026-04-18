"""
Lectures centralisées sur PostgreSQL.

Toutes les requêtes SQL de lecture sont ici. Le pipeline et les routes ne
font jamais de requêtes directes sur les modèles ORM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

# La DB peut stocker les timestamps en tz-naïf (SQLite) ou tz-aware (PostgreSQL).
# On convertit systématiquement en naïf pour comparer aux clés du dict.
from optimizer.db.models import (
    ConsumptionForecast,
    PVProductionForecast,
    Site,
    SpotPriceForecast,
    Trajectoire,
    TrajectoirePas,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrevisionPoint:
    """Un point de prévision ou de prix, avec indicateur de fallback."""

    timestamp: datetime
    valeur: float
    est_fallback: bool = False


def get_site(session: Session, site_id: str) -> Site | None:
    """Retourne le site ou None s'il n'existe pas."""
    return session.query(Site).filter(Site.site_id == site_id).one_or_none()


def _strip_tz(ts: datetime) -> datetime:
    """Retire le tzinfo pour une comparaison homogène avec les valeurs lues en DB."""
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _fetch_most_recent_forecasts(
    session: Session,
    model: type[ConsumptionForecast] | type[PVProductionForecast],
    site_id: str,
    debut: datetime,
    fin: datetime,
) -> dict[datetime, float]:
    """
    Charge les prévisions pour la fenêtre [debut, fin).

    Si plusieurs `date_generation` couvrent le même timestamp, on retient la
    plus récente (déduplication en Python pour rester compatible SQLite).
    """
    rows = (
        session.query(model.timestamp, model.puissance_kw, model.date_generation)
        .filter(model.site_id == site_id)
        .filter(model.timestamp >= debut)
        .filter(model.timestamp < fin)
        .all()
    )
    plus_recents: dict[datetime, tuple[float, datetime]] = {}
    for ts, puissance, gen in rows:
        cle = _strip_tz(ts)
        existant = plus_recents.get(cle)
        if existant is None or existant[1] < gen:
            plus_recents[cle] = (float(puissance), gen)
    return {ts: valeur for ts, (valeur, _) in plus_recents.items()}


def get_forecast_consommation(
    session: Session,
    site_id: str,
    debut: datetime,
    fin: datetime,
    timestamps_attendus: list[datetime],
) -> list[PrevisionPoint]:
    """
    Retourne les prévisions de consommation pour la fenêtre [debut, fin).

    Les pas manquants sont reportés avec `est_fallback=True` et `valeur=0.0`
    (c'est au pipeline de vérifier le taux de manquants et de lever une erreur
    si > 50 %).
    """
    connus = _fetch_most_recent_forecasts(session, ConsumptionForecast, site_id, debut, fin)
    return [
        PrevisionPoint(timestamp=ts, valeur=connus[_strip_tz(ts)], est_fallback=False)
        if _strip_tz(ts) in connus
        else PrevisionPoint(timestamp=ts, valeur=0.0, est_fallback=True)
        for ts in timestamps_attendus
    ]


def get_forecast_production_pv(
    session: Session,
    site_id: str,
    debut: datetime,
    fin: datetime,
    timestamps_attendus: list[datetime],
) -> list[PrevisionPoint]:
    """Retourne les prévisions PV pour la fenêtre [debut, fin)."""
    connus = _fetch_most_recent_forecasts(session, PVProductionForecast, site_id, debut, fin)
    return [
        PrevisionPoint(timestamp=ts, valeur=connus[_strip_tz(ts)], est_fallback=False)
        if _strip_tz(ts) in connus
        else PrevisionPoint(timestamp=ts, valeur=0.0, est_fallback=True)
        for ts in timestamps_attendus
    ]


def _prix_a_timestamp(session: Session, site_id: str, ts: datetime) -> float | None:
    """Retourne le prix spot exact à ce timestamp, ou None."""
    row = (
        session.query(SpotPriceForecast.prix_eur_mwh)
        .filter(SpotPriceForecast.site_id == site_id)
        .filter(SpotPriceForecast.timestamp == ts)
        .order_by(SpotPriceForecast.date_generation.desc())
        .first()
    )
    return float(row[0]) if row else None


def _moyenne_creneau_4_semaines(session: Session, site_id: str, ts: datetime) -> float | None:
    """
    Moyenne des prix spots sur le même créneau horaire (HH:MM) lors des
    4 dernières semaines avant `ts`. Retourne None si aucune donnée.
    """
    candidats = [ts - timedelta(weeks=k) for k in range(1, 5)]
    row = (
        session.query(func.avg(SpotPriceForecast.prix_eur_mwh))
        .filter(SpotPriceForecast.site_id == site_id)
        .filter(SpotPriceForecast.timestamp.in_(candidats))
        .scalar()
    )
    return float(row) if row is not None else None


def get_prix_spots(
    session: Session,
    site_id: str,
    timestamps_attendus: list[datetime],
    prix_defaut_eur_mwh: float,
) -> list[PrevisionPoint]:
    """
    Retourne les prix spots pour chaque timestamp attendu, avec fallback :

    1. Prix exact pour ce timestamp.
    2. Prix à timestamp - 7j (même jour de la semaine).
    3. Moyenne des 4 dernières semaines sur le même créneau horaire.
    4. Valeur par défaut `prix_defaut_eur_mwh`.

    `est_fallback=True` dès que le prix ne provient pas de la DB pour ce
    timestamp exact.
    """
    points: list[PrevisionPoint] = []
    nb_fallback = 0

    for ts in timestamps_attendus:
        prix = _prix_a_timestamp(session, site_id, ts)
        if prix is not None:
            points.append(PrevisionPoint(timestamp=ts, valeur=prix, est_fallback=False))
            continue

        prix_j7 = _prix_a_timestamp(session, site_id, ts - timedelta(days=7))
        if prix_j7 is not None:
            nb_fallback += 1
            points.append(PrevisionPoint(timestamp=ts, valeur=prix_j7, est_fallback=True))
            continue

        moyenne = _moyenne_creneau_4_semaines(session, site_id, ts)
        if moyenne is not None:
            nb_fallback += 1
            points.append(PrevisionPoint(timestamp=ts, valeur=moyenne, est_fallback=True))
            continue

        nb_fallback += 1
        points.append(PrevisionPoint(timestamp=ts, valeur=prix_defaut_eur_mwh, est_fallback=True))

    if nb_fallback:
        logger.info(
            "get_prix_spots | site=%s | %d/%d pas en fallback",
            site_id,
            nb_fallback,
            len(timestamps_attendus),
        )
    return points


def get_derniere_trajectoire(session: Session, site_id: str) -> Trajectoire | None:
    """Retourne la dernière trajectoire calculée pour ce site, ou None."""
    return (
        session.query(Trajectoire)
        .filter(Trajectoire.site_id == site_id)
        .order_by(Trajectoire.timestamp_calcul.desc())
        .first()
    )


def get_pas_trajectoire(session: Session, trajectoire_id: int) -> list[TrajectoirePas]:
    """Retourne les pas d'une trajectoire, triés par timestamp."""
    return (
        session.query(TrajectoirePas)
        .filter(TrajectoirePas.trajectoire_id == trajectoire_id)
        .order_by(TrajectoirePas.timestamp)
        .all()
    )
