"""
Tests de l'écriture des trajectoires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from optimizer.db import readers, writers


def test_save_trajectoire_persiste_pas_avec_cascade(db_session, sample_site):
    pas = [
        writers.PasTrajectoireNouveau(
            timestamp=datetime(2026, 4, 18, 10, tzinfo=UTC) + timedelta(minutes=15 * i),
            energie_kwh=float(i),
            soe_cible_kwh=100.0 + i,
        )
        for i in range(4)
    ]
    traj = writers.save_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 10, 0, 5, tzinfo=UTC),
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=datetime(2026, 4, 18, 10, tzinfo=UTC),
        horizon_fin=datetime(2026, 4, 20, 10, tzinfo=UTC),
        pas=pas,
    )
    assert traj.id is not None
    relus = readers.get_pas_trajectoire(db_session, traj.id)
    assert len(relus) == 4
    assert [p.soe_cible_kwh for p in relus] == [100.0, 101.0, 102.0, 103.0]


def test_derniere_trajectoire_renvoie_la_plus_recente(db_session, sample_site):
    for heure in (9, 10, 11):
        writers.save_trajectoire(
            db_session,
            site_id=sample_site.site_id,
            timestamp_calcul=datetime(2026, 4, 18, heure, tzinfo=UTC),
            soe_initial_kwh=100.0,
            statut="ok",
            message=None,
            derive_pct=None,
            horizon_debut=datetime(2026, 4, 18, heure, tzinfo=UTC),
            horizon_fin=datetime(2026, 4, 20, heure, tzinfo=UTC),
            pas=[],
        )
    derniere = readers.get_derniere_trajectoire(db_session, sample_site.site_id)
    assert derniere is not None
    # SQLite stocke tz-naïf ; on compare sur les composantes pour être agnostique.
    assert derniere.timestamp_calcul.replace(tzinfo=None) == datetime(2026, 4, 18, 11)
