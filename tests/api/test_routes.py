"""
Tests des routes FastAPI — pipeline mocké pour isoler la couche HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from optimizer.api import routes
from optimizer.config import ConfigYaml
from optimizer.db.session import get_session
from optimizer.exceptions import ForecastsMissingError, SiteNotFoundError
from optimizer.main import app
from optimizer.optimizer.types import PasSolveur
from optimizer.pipeline.optimize import STATUT_OK, ResultatOptimisation


@pytest.fixture
def client(db_session, cfg_test):
    """TestClient avec session DB injectée et config.yaml mocké."""

    def override_get_session():
        yield db_session

    def override_get_config() -> ConfigYaml:
        return cfg_test

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[routes.get_config] = override_get_config
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(site_id: str = "site-test-01"):
    return {
        "site_id": site_id,
        "soe_actuel_kwh": 100.0,
        "capacite_bess_kwh": 200.0,
    }


def test_post_optimize_422_si_payload_invalide(client):
    mauvais = {"site_id": "x", "soe_actuel_kwh": -1.0}  # soe_actuel négatif
    response = client.post("/api/v1/optimize", json=mauvais)
    assert response.status_code == 422


def test_post_optimize_404_si_site_inconnu(client, monkeypatch):
    def fake_run(**_):
        raise SiteNotFoundError("inconnu")

    monkeypatch.setattr(routes.pipeline, "run_optimization", fake_run)
    response = client.post("/api/v1/optimize", json=_payload())
    assert response.status_code == 404


def test_post_optimize_503_si_forecasts_manquants(client, monkeypatch):
    def fake_run(**_):
        raise ForecastsMissingError("forecasts KO")

    monkeypatch.setattr(routes.pipeline, "run_optimization", fake_run)
    response = client.post("/api/v1/optimize", json=_payload())
    assert response.status_code == 503


def test_post_optimize_happy_path(client, monkeypatch):
    debut = datetime(2026, 4, 18, 10, tzinfo=UTC)
    pas = [
        PasSolveur(
            timestamp=debut + timedelta(minutes=15 * i),
            energie_kwh=float(i),
            soe_cible_kwh=100.0 + i,
        )
        for i in range(96)
    ]

    def fake_run(**_):
        return ResultatOptimisation(
            site_id="site-test-01",
            timestamp_calcul=debut,
            horizon_debut=debut,
            horizon_fin=debut + timedelta(hours=24),
            statut=STATUT_OK,
            message="",
            derive_pct=None,
            pas_reponse=pas,
        )

    monkeypatch.setattr(routes.pipeline, "run_optimization", fake_run)
    response = client.post("/api/v1/optimize", json=_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["statut"] == "ok"
    assert len(data["trajectoire"]) == 96


def test_get_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
