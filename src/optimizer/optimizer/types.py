"""
Types internes échangés entre le pipeline et le solveur.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SiteParams:
    """Paramètres du site nécessaires au solveur (convention producteur)."""

    site_id: str
    capacite_bess_kwh: float
    p_max_bess_kw: float
    p_souscrite_kw: float
    soc_min_pct: float
    soc_max_pct: float
    p_max_injection_kw: float
    p_max_soutirage_kw: float
    rendement_bess: float


@dataclass(frozen=True)
class SolverInput:
    """
    Entrée du solveur — toutes les séries ont la même longueur (nb_pas).
    Tous les timestamps sont alignés sur le début de la fenêtre interne.
    """

    site: SiteParams
    soc_initial_kwh: float
    timestamps: list[datetime]
    conso_kw: list[float]
    pv_kw: list[float]
    prix_eur_mwh: list[float]
    pas_heure: float
    slack_penalty_eur_par_kwh: float


@dataclass(frozen=True)
class PasSolveur:
    """Un pas de la trajectoire solveur."""

    timestamp: datetime
    energie_kwh: float  # convention producteur : positif = décharge
    soc_cible_kwh: float


@dataclass(frozen=True)
class SolverOutput:
    """Résultat du solveur."""

    pas: list[PasSolveur]
    slack_total_kwh: float
    cout_total_eur: float
    solver_status: str
