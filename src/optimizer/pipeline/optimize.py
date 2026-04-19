"""
Orchestration : lit DB → calcule la dérive → résout le LP → écrit la trajectoire.

Point d'entrée appelé par la route `POST /api/v1/optimize`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from optimizer.config import ConfigYaml
from optimizer.db import readers, writers
from optimizer.db.models import Site
from optimizer.exceptions import ForecastsMissingError, SiteNotFoundError
from optimizer.optimizer import solver
from optimizer.optimizer.types import PasSolveur, SiteParams, SolverInput, SolverOutput
from optimizer.pipeline.drift import calcul_derive_pct

logger = logging.getLogger(__name__)

STATUT_OK = "ok"
STATUT_CORRECTIVE = "corrective"
STATUT_DEGRADED = "degraded"


@dataclass(frozen=True)
class ResultatOptimisation:
    """Résultat renvoyé par `run_optimization` au layer API."""

    site_id: str
    timestamp_calcul: datetime
    horizon_debut: datetime
    horizon_fin: datetime
    statut: str
    message: str
    derive_pct: float | None
    pas_reponse: list[PasSolveur]  # tronqué à horizon_reponse_h


def _floor_pas(ts: datetime, pas_minutes: int) -> datetime:
    """Arrondit `ts` au multiple inférieur de pas_minutes (ex. 10:07 → 10:00)."""
    minute = (ts.minute // pas_minutes) * pas_minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


def _to_site_params(site: Site) -> SiteParams:
    """Convertit un Site ORM en SiteParams pour le solveur."""
    return SiteParams(
        site_id=site.site_id,
        capacite_bess_kwh=float(site.capacite_bess_kwh),
        p_max_bess_kw=float(site.p_max_bess_kw),
        p_souscrite_kw=float(site.p_souscrite_kw),
        soc_min_pct=float(site.soc_min_pct),
        soc_max_pct=float(site.soc_max_pct),
        p_max_injection_kw=float(site.p_max_injection_kw),
        p_max_soutirage_kw=float(
            site.p_max_soutirage_kw if site.p_max_soutirage_kw is not None else site.p_souscrite_kw
        ),
        rendement_bess=float(site.rendement_bess),
    )


def _choisir_statut(
    output: SolverOutput,
    derive_pct: float | None,
    cfg: ConfigYaml,
) -> tuple[str, str]:
    """Détermine le statut final et le message associé."""
    if output.slack_total_kwh > cfg.seuil_slack_kwh:
        return (
            STATUT_DEGRADED,
            f"Contrainte de puissance souscrite violée ({output.slack_total_kwh:.2f} kWh cumulés).",
        )
    if derive_pct is not None and derive_pct > cfg.seuil_derive_pct:
        return (
            STATUT_CORRECTIVE,
            f"Dérive SoC {derive_pct:.1f}% > seuil {cfg.seuil_derive_pct}%.",
        )
    return STATUT_OK, ""


def run_optimization(
    session: Session,
    site_id: str,
    soc_actuel_kwh: float,
    timestamp_requete: datetime,
    cfg: ConfigYaml,
) -> ResultatOptimisation:
    """
    Orchestre l'optimisation pour un site donné.

    Étapes :
    1. Lire `Site` (lève `SiteNotFoundError` si inconnu → 404).
    2. Construire la fenêtre horizon_interne (N pas).
    3. Lire forecasts conso + PV ; lever `ForecastsMissingError` si > 50 % manquants.
    4. Lire les prix spots (avec fallback J-7 → moyenne 4 sem. → défaut).
    5. Calculer la dérive vs la trajectoire précédente.
    6. Résoudre le LP.
    7. Déterminer le statut, écrire en DB, retourner les 96 premiers pas.
    """
    t0 = time.perf_counter()
    logger.info("run_optimization START | site=%s | soc=%.1f kWh", site_id, soc_actuel_kwh)

    site = readers.get_site(session, site_id)
    if site is None:
        raise SiteNotFoundError(f"site_id inconnu : {site_id}")

    horizon_debut = _floor_pas(timestamp_requete, cfg.pas_minutes)
    horizon_fin = horizon_debut + timedelta(hours=cfg.horizon_interne_h)
    pas_delta = timedelta(minutes=cfg.pas_minutes)
    timestamps = [horizon_debut + i * pas_delta for i in range(cfg.nb_pas_interne)]

    conso = readers.get_forecast_consommation(
        session, site_id, horizon_debut, horizon_fin, timestamps
    )
    pv = readers.get_forecast_production_pv(
        session, site_id, horizon_debut, horizon_fin, timestamps
    )
    manquant_conso = sum(1 for p in conso if p.est_fallback) / len(conso)
    manquant_pv = sum(1 for p in pv if p.est_fallback) / len(pv)
    if manquant_conso > 0.5 or manquant_pv > 0.5:
        raise ForecastsMissingError(
            f"Trop de forecasts manquants (conso={manquant_conso:.0%}, pv={manquant_pv:.0%})."
        )

    prix = readers.get_prix_spots(session, site_id, timestamps, cfg.prix_spot_defaut_eur_mwh)

    nb_conso_ok = sum(1 for p in conso if not p.est_fallback)
    nb_pv_ok = sum(1 for p in pv if not p.est_fallback)
    nb_prix_ok = sum(1 for p in prix if not p.est_fallback)
    logger.info(
        "forecasts chargés | site=%s | conso=%d/%d | pv=%d/%d | prix=%d/%d",
        site_id,
        nb_conso_ok, len(conso),
        nb_pv_ok, len(pv),
        nb_prix_ok, len(prix),
    )

    capacite_bess = float(site.capacite_bess_kwh)
    derniere = readers.get_derniere_trajectoire(session, site_id)
    derive_pct = calcul_derive_pct(
        session, derniere, soc_actuel_kwh, timestamp_requete, capacite_bess
    )

    entree = SolverInput(
        site=_to_site_params(site),
        soc_initial_kwh=soc_actuel_kwh,
        timestamps=timestamps,
        conso_kw=[p.valeur for p in conso],
        pv_kw=[p.valeur for p in pv],
        prix_eur_mwh=[p.valeur for p in prix],
        pas_heure=cfg.pas_heure,
        slack_penalty_eur_par_kwh=cfg.slack_penalty_eur_par_kwh,
    )
    sortie = solver.solve(entree)
    statut, message = _choisir_statut(sortie, derive_pct, cfg)

    timestamp_calcul = datetime.now(tz=UTC)
    writers.save_trajectoire(
        session,
        site_id=site_id,
        timestamp_calcul=timestamp_calcul,
        soc_initial_kwh=soc_actuel_kwh,
        statut=statut,
        message=message or None,
        derive_pct=derive_pct,
        horizon_debut=horizon_debut,
        horizon_fin=horizon_fin,
        pas=[
            writers.PasTrajectoireNouveau(
                timestamp=p.timestamp,
                energie_kwh=p.energie_kwh,
                soc_cible_kwh=p.soc_cible_kwh,
            )
            for p in sortie.pas
        ],
    )

    logger.info(
        "run_optimization END | site=%s | statut=%s | derive=%s | cout=%.2f | %.0fms",
        site_id,
        statut,
        f"{derive_pct:.1f}%" if derive_pct is not None else "n/a",
        sortie.cout_total_eur,
        (time.perf_counter() - t0) * 1000,
    )

    pas_reponse = sortie.pas[: cfg.nb_pas_reponse]
    return ResultatOptimisation(
        site_id=site_id,
        timestamp_calcul=timestamp_calcul,
        horizon_debut=horizon_debut,
        horizon_fin=horizon_debut + timedelta(hours=cfg.horizon_reponse_h),
        statut=statut,
        message=message,
        derive_pct=derive_pct,
        pas_reponse=pas_reponse,
    )
