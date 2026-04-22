"""
Tests des readers — en particulier le fallback prix spots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from optimizer.db import readers
from optimizer.db.models import SpotPriceForecast
from optimizer.exceptions import PrixSpotsIndisponibles


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

    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts])
    assert len(points) == 1
    assert points[0].valeur == 123.0
    assert points[0].est_fallback is False


def test_prix_spots_fallback_j_moins_1(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    _ajouter_prix(db_session, sample_site.site_id, ts - timedelta(days=1), 55.0)

    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts])
    assert points[0].valeur == 55.0
    assert points[0].est_fallback is True


def test_prix_spots_fallback_j_moins_2(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
    _ajouter_prix(db_session, sample_site.site_id, ts - timedelta(days=2), 42.0)

    points = readers.get_prix_spots(db_session, sample_site.site_id, [ts])
    assert points[0].valeur == 42.0
    assert points[0].est_fallback is True


def test_prix_spots_leve_exception_si_aucun_prix(db_session, sample_site):
    ts = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)

    with pytest.raises(PrixSpotsIndisponibles):
        readers.get_prix_spots(db_session, sample_site.site_id, [ts])


def test_get_site_present_vs_absent(db_session, sample_site):
    assert readers.get_site(db_session, sample_site.site_id) is not None
    assert readers.get_site(db_session, "inconnu") is None
