"""
Tests des readers — en particulier le fallback prix spots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from optimizer.db import readers
from optimizer.db.models import SpotPriceForecast


def _ajouter_prix(session, site_id: str, ts: datetime, prix: float) -> None:
    session.add(
        SpotPriceForecast(
            site_id=site_id,
            timestamp=ts,
            prix_eur_mwh=prix,
            date_generation=datetime(2026, 4, 18, tzinfo=UTC),
            source="RTE",
        )
    )
    session.flush()


def test_prix_spots_exact_match(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    _ajouter_prix(db_session, sample_site.site_id, ts, 123.0)

    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts], prix_defaut_eur_mwh=80.0)
    assert len(points) == 1
    assert points[0].valeur == 123.0
    assert points[0].est_fallback is False


def test_prix_spots_fallback_j_moins_7(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    ts_7j = ts - timedelta(days=7)
    _ajouter_prix(db_session, sample_site.site_id, ts_7j, 55.0)

    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts], prix_defaut_eur_mwh=80.0)
    assert points[0].valeur == 55.0
    assert points[0].est_fallback is True


def test_prix_spots_fallback_moyenne_4_semaines(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    for k in (1, 2, 3, 4):
        _ajouter_prix(
            db_session,
            sample_site.site_id,
            ts - timedelta(weeks=k) - timedelta(minutes=1),  # pas sur ts exact ni ts-7j
            100.0 * k,
        )
    # Ajout aux timestamps ts - k semaines exacts (pour la moyenne 4 sem. HH:MM match)
    for k in (2, 3, 4):
        _ajouter_prix(db_session, sample_site.site_id, ts - timedelta(weeks=k), 100.0 * k)
    # Pas d'ajout à ts - 1 semaine (donc fallback 1 échoue, on passe au fallback 2).
    # La moyenne des 3 valeurs (k=2,3,4) = (200+300+400)/3 = 300
    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts], prix_defaut_eur_mwh=80.0)
    assert points[0].est_fallback is True
    assert abs(points[0].valeur - 300.0) < 1e-6


def test_prix_spots_fallback_defaut(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts], prix_defaut_eur_mwh=77.0)
    assert points[0].valeur == 77.0
    assert points[0].est_fallback is True


def test_get_site_present_vs_absent(db_session, sample_site):
    assert readers.get_site(db_session, sample_site.site_id) is not None
    assert readers.get_site(db_session, "inconnu") is None
