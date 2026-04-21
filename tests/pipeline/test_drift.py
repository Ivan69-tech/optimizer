"""
Tests du calcul de dérive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from optimizer.db.models import Trajectoire, TrajectoirePas
from optimizer.pipeline.drift import calcul_derive_pct


def _insert_traj(db_session, sample_site, horizon_debut, pas_data):
    """Insère une trajectoire + ses pas dans la DB."""
    traj = Trajectoire(
        site_id=sample_site.site_id,
        timestamp_calcul=horizon_debut,
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=horizon_debut,
        horizon_fin=horizon_debut + timedelta(hours=48),
    )
    db_session.add(traj)
    now = datetime.now(tz=UTC)
    for ts, soe in pas_data:
        db_session.add(
            TrajectoirePas(
                site_id=sample_site.site_id,
                timestamp=ts,
                energie_kwh=0.0,
                soe_cible_kwh=soe,
                insertion_timestamp=now,
            )
        )
    db_session.flush()
    return traj


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
    t_pas = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    traj = _insert_traj(
        db_session,
        sample_site,
        horizon_debut=datetime(2026, 4, 18, 9, tzinfo=UTC),
        pas_data=[(t_pas, 100.0)],
    )

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
    t0 = datetime(2026, 4, 18, 9, 0, tzinfo=UTC)
    pas_data = [(t0 + timedelta(minutes=15 * i), 100.0) for i in range(4)]
    traj = _insert_traj(
        db_session,
        sample_site,
        horizon_debut=t0,
        pas_data=pas_data,
    )

    derive = calcul_derive_pct(
        db_session,
        trajectoire_precedente=traj,
        soe_actuel_kwh=80.0,
        timestamp_requete=datetime(2026, 4, 18, 9, 30, tzinfo=UTC),
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
    )
    assert derive is not None
    assert abs(derive - 10.0) < 1e-6
