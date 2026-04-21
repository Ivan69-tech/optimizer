"""
Tests de l'écriture des trajectoires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from optimizer.db import readers, writers


def test_save_trajectoire_ecrase_futur_preserve_passe(db_session, sample_site):
    """Deux saves successifs : les pas futurs sont écrasés, pas de doublon."""
    t0 = datetime(2026, 4, 18, 10, tzinfo=UTC)

    pas_premier = [
        writers.PasTrajectoireNouveau(
            timestamp=t0 + timedelta(minutes=15 * i),
            energie_kwh=float(i),
            soe_cible_kwh=100.0 + i,
        )
        for i in range(4)
    ]
    writers.save_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 10, 0, 5, tzinfo=UTC),
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=t0,
        horizon_fin=t0 + timedelta(hours=48),
        pas=pas_premier,
    )

    # Second save à partir du pas 2 — doit écraser les pas 2 et 3
    t2 = t0 + timedelta(minutes=30)
    pas_second = [
        writers.PasTrajectoireNouveau(
            timestamp=t2 + timedelta(minutes=15 * i),
            energie_kwh=float(i + 10),
            soe_cible_kwh=200.0 + i,
        )
        for i in range(2)
    ]
    writers.save_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 10, 30, 5, tzinfo=UTC),
        soe_initial_kwh=102.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=t2,
        horizon_fin=t2 + timedelta(hours=48),
        pas=pas_second,
    )

    relus = readers.get_pas_trajectoire(db_session, sample_site.site_id)
    # Pas 0 et 1 (passés) + pas 2 et 3 réinsérés (second save)
    assert len(relus) == 4
    # Les deux premiers pas sont ceux du premier save
    assert relus[0].soe_cible_kwh == 100.0
    assert relus[1].soe_cible_kwh == 101.0
    # Les deux suivants viennent du second save
    assert relus[2].soe_cible_kwh == 200.0
    assert relus[3].soe_cible_kwh == 201.0


def test_save_trajectoire_persiste_pas(db_session, sample_site):
    t0 = datetime(2026, 4, 18, 10, tzinfo=UTC)
    pas = [
        writers.PasTrajectoireNouveau(
            timestamp=t0 + timedelta(minutes=15 * i),
            energie_kwh=float(i),
            soe_cible_kwh=100.0 + i,
        )
        for i in range(4)
    ]
    writers.save_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=datetime(2026, 4, 18, 10, 0, 5, tzinfo=UTC),
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=t0,
        horizon_fin=t0 + timedelta(hours=48),
        pas=pas,
    )
    relus = readers.get_pas_trajectoire(db_session, sample_site.site_id)
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
