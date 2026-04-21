"""
Tests du solveur LP — données synthétiques, pas d'accès DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from optimizer.exceptions import InfeasibleProblemError
from optimizer.optimizer.solver import solve
from optimizer.optimizer.types import SiteParams, SolverInput

N = 192  # 48 h × 4
PAS_H = 0.25


def _site(p_max_injection_kw: float = 120.0, p_max_soutirage_kw: float = 150.0) -> SiteParams:
    return SiteParams(
        site_id="site-test-01",
        capacite_bess_kwh=200.0,
        p_max_bess_kw=100.0,
        p_souscrite_kw=150.0,
        p_max_injection_kw=p_max_injection_kw,
        p_max_soutirage_kw=p_max_soutirage_kw,
        rendement_bess=0.95,
    )


def _timestamps(n: int = N) -> list[datetime]:
    debut = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    return [debut + i * timedelta(minutes=15) for i in range(n)]


def _input(site: SiteParams, *, conso: float, pv: float, prix: float) -> SolverInput:
    return SolverInput(
        site=site,
        soe_initial_kwh=100.0,
        timestamps=_timestamps(),
        conso_kw=[conso] * N,
        pv_kw=[pv] * N,
        prix_eur_mwh=[prix] * N,
        pas_heure=PAS_H,
        slack_penalty_eur_par_kwh=1_000_000.0,
    )


def test_bornes_soe_respectees():
    """Le SoE doit rester dans [0, capacite_bess_kwh] à chaque pas."""
    entree = _input(_site(), conso=50.0, pv=0.0, prix=80.0)
    sortie = solve(entree)

    tol = 1e-3
    for pas in sortie.pas:
        assert -tol <= pas.soe_cible_kwh <= 200.0 + tol


def test_bilan_puissance_convention_producteur():
    """Vérifie P_pdl = P_pv + P_bess - P_conso à tous les pas."""
    entree = _input(_site(), conso=60.0, pv=20.0, prix=80.0)
    sortie = solve(entree)

    for i, pas in enumerate(sortie.pas):
        p_bess = pas.energie_kwh / PAS_H  # kW, convention producteur
        p_pdl = entree.pv_kw[i] + p_bess - entree.conso_kw[i]
        # p_pdl borné entre -p_max_soutirage et p_max_injection
        assert p_pdl <= entree.site.p_max_injection_kw + 1e-3
        assert p_pdl >= -entree.site.p_max_soutirage_kw - 1e-3


def test_site_sans_injection_ne_produit_pas_ppdl_positif():
    """p_max_injection_kw = 0 → P_pdl(t) ≤ 0 à tous les pas."""
    # PV surplus en milieu de journée uniquement — sans injection autorisée,
    # la batterie doit absorber tout l'excédent.
    site = _site(p_max_injection_kw=0.0)
    conso = [10.0] * N
    # Pic PV de 60 kW sur 10 pas (2,5 h), nul sinon.
    pv = [60.0 if 20 <= i < 30 else 0.0 for i in range(N)]
    entree = SolverInput(
        site=site,
        soe_initial_kwh=40.0,  # laisse de la marge pour absorber (SoE_max = 200)
        timestamps=_timestamps(),
        conso_kw=conso,
        pv_kw=pv,
        prix_eur_mwh=[80.0] * N,
        pas_heure=PAS_H,
        slack_penalty_eur_par_kwh=1_000_000.0,
    )
    sortie = solve(entree)

    for i, pas in enumerate(sortie.pas):
        p_bess = pas.energie_kwh / PAS_H
        p_pdl = entree.pv_kw[i] + p_bess - entree.conso_kw[i]
        assert p_pdl <= 1e-3, f"P_pdl={p_pdl:.3f} > 0 au pas {i} (injection interdite)"


def test_statut_degraded_si_slack_actif():
    """Si conso - pv > p_souscrite + p_max_bess et SoC bas, slack > 0."""
    # p_max_soutirage généreux, p_souscrite restrictif : la contrainte contractuelle
    # est la première qui cède.
    site = SiteParams(
        site_id="site-test-01",
        capacite_bess_kwh=200.0,
        p_max_bess_kw=100.0,
        p_souscrite_kw=100.0,
        p_max_injection_kw=0.0,
        p_max_soutirage_kw=500.0,  # pipe physique large : la limite c'est le contrat
        rendement_bess=0.95,
    )
    entree = SolverInput(
        site=site,
        soe_initial_kwh=20.0,  # SoE bas → peu de décharge possible
        timestamps=_timestamps(),
        conso_kw=[250.0] * N,  # 250 > p_souscrite (100) + p_max_bess (100) = 200
        pv_kw=[0.0] * N,
        prix_eur_mwh=[80.0] * N,
        pas_heure=PAS_H,
        slack_penalty_eur_par_kwh=1_000_000.0,
    )
    sortie = solve(entree)
    assert sortie.slack_total_kwh > 0.1


def test_solveur_leve_si_soe_initial_hors_bornes():
    """SoE initial > capacite_bess_kwh → problème infaisable (la borne SoE n'a pas de slack)."""
    entree = SolverInput(
        site=_site(),
        soe_initial_kwh=1000.0,  # 500% de la capacité
        timestamps=_timestamps(),
        conso_kw=[50.0] * N,
        pv_kw=[0.0] * N,
        prix_eur_mwh=[80.0] * N,
        pas_heure=PAS_H,
        slack_penalty_eur_par_kwh=1_000_000.0,
    )
    with pytest.raises(InfeasibleProblemError):
        solve(entree)


def test_solveur_preferera_charger_quand_prix_bas_et_decharger_quand_prix_eleve():
    """Arbitrage tarifaire : heures chères → décharge, heures creuses → charge."""
    # Prix : 6h pleines à 200 €/MWh, 6h creuses à 10 €/MWh, alternés.
    prix = []
    for i in range(N):
        prix.append(200.0 if (i // 24) % 2 == 0 else 10.0)

    entree = SolverInput(
        site=_site(),
        soe_initial_kwh=100.0,
        timestamps=_timestamps(),
        conso_kw=[30.0] * N,
        pv_kw=[0.0] * N,
        prix_eur_mwh=prix,
        pas_heure=PAS_H,
        slack_penalty_eur_par_kwh=1_000_000.0,
    )
    sortie = solve(entree)

    # Décharge moyenne pendant les heures chères > décharge pendant les heures creuses.
    energie_pleine = [p.energie_kwh for i, p in enumerate(sortie.pas) if (i // 24) % 2 == 0]
    energie_creuse = [p.energie_kwh for i, p in enumerate(sortie.pas) if (i // 24) % 2 == 1]
    assert sum(energie_pleine) > sum(energie_creuse)
