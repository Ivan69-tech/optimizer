"""
Solveur LP de trajectoire énergétique BESS — formulation CVXPY + HiGHS.

Convention producteur : `energie_kwh > 0` = décharge batterie (injection
sur le bus). Voir CLAUDE.md §Formulation pour le détail mathématique.
"""

from __future__ import annotations

import logging
import time

import cvxpy as cp
import numpy as np

from optimizer.exceptions import InfeasibleProblemError
from optimizer.optimizer.types import PasSolveur, SolverInput, SolverOutput

logger = logging.getLogger(__name__)


def solve(entree: SolverInput) -> SolverOutput:
    """
    Résout le LP de trajectoire optimale sur l'horizon interne.

    Variables :
        e_charge[t]   ≥ 0   kWh absorbés par la batterie
        e_decharge[t] ≥ 0   kWh restitués par la batterie
        slack[t]      ≥ 0   dépassement (kW) de la puissance souscrite, pénalisé

    Contraintes : voir CLAUDE.md §Contraintes.
    """
    site = entree.site
    n = len(entree.timestamps)
    assert len(entree.conso_kw) == n == len(entree.pv_kw) == len(entree.prix_eur_mwh)

    pas_h = entree.pas_heure
    eta = site.rendement_bess

    conso = np.asarray(entree.conso_kw, dtype=float)
    pv = np.asarray(entree.pv_kw, dtype=float)
    prix_eur_kwh = np.asarray(entree.prix_eur_mwh, dtype=float) / 1000.0

    soc_min = 0.0
    soc_max = site.capacite_bess_kwh
    e_bess_max = site.p_max_bess_kw * pas_h  # kWh max par pas

    e_charge = cp.Variable(n, nonneg=True)
    e_decharge = cp.Variable(n, nonneg=True)
    slack = cp.Variable(n, nonneg=True)

    # Puissance nette BESS (kW) — convention producteur : positif = décharge.
    p_bess = (e_decharge - e_charge) / pas_h
    # Bilan PDL en convention producteur : P_pdl = P_pv + P_bess - P_conso.
    p_pdl = pv + p_bess - conso

    # Dynamique SoC : SoC(t+1) = SoC(t) + e_charge * eta - e_decharge / eta.
    # Pour éviter une variable SoC supplémentaire, on exprime SoC(t) comme
    # SoC(0) + cumsum(e_charge * eta - e_decharge / eta) et on contraint
    # les bornes directement.
    delta_soc = e_charge * eta - e_decharge / eta
    # soc_apres_pas[t] = SoC après application du pas t  (kWh), t = 0..n-1
    # soc_initial est la valeur de SoC(0) avant tout pas.
    soc_apres_pas = entree.soc_initial_kwh + cp.cumsum(delta_soc)

    contraintes = [
        # Bornes SoC sur tous les états intermédiaires et final.
        soc_apres_pas >= soc_min,
        soc_apres_pas <= soc_max,
        # Puissance BESS max (charge et décharge séparément).
        e_charge <= e_bess_max,
        e_decharge <= e_bess_max,
        # Bornes PDL — convention producteur.
        p_pdl <= site.p_max_injection_kw,
        p_pdl >= -site.p_max_soutirage_kw,
        # Puissance souscrite : soutirage borné avec slack (en kW).
        p_pdl >= -site.p_souscrite_kw - slack,
    ]

    # Coût : ce qu'on paye au réseau (soutirage) - ce qu'on reçoit (injection).
    # cost(t) = -p_pdl(t) * prix * pas_h  (€).
    cout_reseau = cp.sum(cp.multiply(-p_pdl, prix_eur_kwh)) * pas_h
    # Slack exprimé en kW ; conversion en kWh-équivalent pour la pénalité.
    penalite = entree.slack_penalty_eur_par_kwh * cp.sum(slack) * pas_h
    objectif = cp.Minimize(cout_reseau + penalite)

    probleme = cp.Problem(objectif, contraintes)
    logger.info("solve START | n_steps=%d | soc_init=%.1f kWh", n, entree.soc_initial_kwh)
    t_solve = time.perf_counter()
    probleme.solve(solver=cp.HIGHS)
    solve_ms = (time.perf_counter() - t_solve) * 1000

    if probleme.status not in ("optimal", "optimal_inaccurate"):
        raise InfeasibleProblemError(f"Solveur non convergent : statut={probleme.status}")

    e_c = np.asarray(e_charge.value, dtype=float).reshape(-1)
    e_d = np.asarray(e_decharge.value, dtype=float).reshape(-1)
    slack_vals = np.asarray(slack.value, dtype=float).reshape(-1)
    # Clamp numérique des valeurs légèrement négatives dues au solveur.
    e_c = np.clip(e_c, 0.0, None)
    e_d = np.clip(e_d, 0.0, None)
    slack_vals = np.clip(slack_vals, 0.0, None)

    energie_kwh = e_d - e_c  # convention producteur
    delta = e_c * eta - e_d / eta
    soc_values = entree.soc_initial_kwh + np.cumsum(delta)

    pas_resultat = [
        PasSolveur(
            timestamp=ts,
            energie_kwh=float(energie_kwh[i]),
            soc_cible_kwh=float(soc_values[i]),
        )
        for i, ts in enumerate(entree.timestamps)
    ]

    slack_total_kwh = float(np.sum(slack_vals) * pas_h)
    # Coût réel : soutirage × prix − injection × prix (en convention producteur).
    p_pdl_vals = pv - conso + (e_d - e_c) / pas_h
    soutirage_kwh = np.clip(-p_pdl_vals, 0.0, None) * pas_h
    injection_kwh = np.clip(p_pdl_vals, 0.0, None) * pas_h
    cout_total_eur = float(
        np.sum(soutirage_kwh * prix_eur_kwh) - np.sum(injection_kwh * prix_eur_kwh)
    )

    logger.info(
        "solve END | status=%s | cout=%.2f EUR | slack_total=%.3f kWh | %.0fms",
        probleme.status,
        cout_total_eur,
        slack_total_kwh,
        solve_ms,
    )

    return SolverOutput(
        pas=pas_resultat,
        slack_total_kwh=slack_total_kwh,
        cout_total_eur=cout_total_eur,
        solver_status=probleme.status,
    )
