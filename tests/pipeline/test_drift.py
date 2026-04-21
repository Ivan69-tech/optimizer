"""
Tests du calcul de dérive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from optimizer.db.models import Trajectoire, TrajectoirePas
from optimizer.pipeline.drift import calcul_derive_pct


def test_derive_sans_trajectoire_precedente_retourne_none(db_session, sample_site):
    derive = calcul_derive_pct(
        db_session,
        trajectoire_precedente=None,
        soe_actuel_kwh=100.0,
        timestamp_requete=datetime(2026, 4, 18, 10, tzinfo=UTC),
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
    )
    assert derive is None


def test_derive_avec_trajectoire_mais_sans_pas_pertinent(db_session, sample_site):
    """Tous les pas sont postérieurs à la requête → pas de pas proche."""
    traj = Trajectoire(
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 9, tzinfo=UTC),
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=datetime(2026, 4, 18, 10, tzinfo=UTC),
        horizon_fin=datetime(2026, 4, 20, 10, tzinfo=UTC),
        pas=[
            TrajectoirePas(
                timestamp=datetime(2026, 4, 18, 10, 0, tzinfo=UTC),
                energie_kwh=0.0,
                soe_cible_kwh=100.0,
            )
        ],
    )
    db_session.add(traj)
    db_session.flush()

    derive = calcul_derive_pct(
        db_session,
        trajectoire_precedente=traj,
        soe_actuel_kwh=100.0,
        timestamp_requete=datetime(2026, 4, 18, 9, 30, tzinfo=UTC),
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
    )
    assert derive is None


def test_derive_calculee_correctement(db_session, sample_site):
    """Écart de 20 kWh sur capacité 200 → dérive 10 %."""
    traj = Trajectoire(
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 9, tzinfo=UTC),
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=datetime(2026, 4, 18, 9, tzinfo=UTC),
        horizon_fin=datetime(2026, 4, 20, 9, tzinfo=UTC),
        pas=[
            TrajectoirePas(
                timestamp=datetime(2026, 4, 18, 9, 0, tzinfo=UTC) + timedelta(minutes=15 * i),
                energie_kwh=0.0,
                soe_cible_kwh=100.0,
            )
            for i in range(4)
        ],
    )
    db_session.add(traj)
    db_session.flush()

    derive = calcul_derive_pct(
        db_session,
        trajectoire_precedente=traj,
        soe_actuel_kwh=80.0,
        timestamp_requete=datetime(2026, 4, 18, 9, 30, tzinfo=UTC),
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
    )
    assert derive is not None
    assert abs(derive - 10.0) < 1e-6
