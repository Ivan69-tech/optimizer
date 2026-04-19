"""
Fixtures pytest communes.

La DB de test est SQLite :memory: — pas besoin de PostgreSQL pour faire tourner
la suite. Chaque test tourne dans une transaction qui est rollback à la fin.
"""

from __future__ import annotations

import os

# Env vars requises avant l'import du package optimizer (Settings est eager).
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CONFIG_PATH", "config.yaml")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from optimizer.config import ConfigYaml
from optimizer.db.models import Base, Site

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(test_engine) -> Session:
    connection = test_engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def sample_site(db_session: Session) -> Site:
    """Site fictif avec tous les champs optimizer renseignés (injection autorisée)."""
    site = Site(
        site_id="site-test-01",
        nom="Site de test",
        capacite_bess_kwh=200.0,
        p_max_bess_kw=100.0,
        p_souscrite_kw=150.0,
        soc_min_pct=10.0,
        soc_max_pct=90.0,
        p_max_injection_kw=120.0,
        p_max_soutirage_kw=150.0,
        rendement_bess=0.95,
    )
    db_session.add(site)
    db_session.flush()
    return site


@pytest.fixture
def sample_site_sans_injection(db_session: Session) -> Site:
    """Site dont l'injection est interdite (p_max_injection_kw = 0)."""
    site = Site(
        site_id="site-test-noinj",
        nom="Site sans injection",
        capacite_bess_kwh=200.0,
        p_max_bess_kw=100.0,
        p_souscrite_kw=150.0,
        soc_min_pct=10.0,
        soc_max_pct=90.0,
        p_max_injection_kw=0.0,
        p_max_soutirage_kw=150.0,
        rendement_bess=0.95,
    )
    db_session.add(site)
    db_session.flush()
    return site


@pytest.fixture
def cfg_test() -> ConfigYaml:
    """Configuration fonctionnelle par défaut pour les tests."""
    return ConfigYaml(
        prix_spot_defaut_eur_mwh=80.0,
        seuil_derive_pct=10.0,
        slack_penalty_eur_par_kwh=1_000_000.0,
        seuil_slack_kwh=0.1,
        horizon_interne_h=48,
        horizon_reponse_h=24,
        pas_minutes=15,
    )
