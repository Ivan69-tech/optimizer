"""
Test end-to-end du pipeline (DB en mémoire + solveur réel).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from optimizer.db.models import ConsumptionForecast, PVProductionForecast, SpotPriceForecast
from optimizer.exceptions import ForecastsMissingError, SiteNotFoundError
from optimizer.pipeline.optimize import (
    STATUT_CORRECTIVE,
    STATUT_OK,
    run_optimization,
)

N = 192


def _debut_test() -> datetime:
    """Retourne l'heure courante arrondie au pas de 15 min (même logique que le pipeline)."""
    now = datetime.now(UTC)
    return now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)


def _remplir_forecasts(db_session, site_id: str, debut: datetime, conso: float, pv: float):
    gen = datetime.now(UTC)
    for i in range(N):
        ts = debut + timedelta(minutes=15 * i)
        db_session.add(
            ConsumptionForecast(
                site_id=site_id,
                timestamp=ts,
                puissance_kw=conso,
                horizon_h=48,
                date_generation=gen,
                version_modele="v-test",
            )
        )
        db_session.add(
            PVProductionForecast(
                site_id=site_id,
                timestamp=ts,
                puissance_kw=pv,
                horizon_h=48,
                date_generation=gen,
                version_modele="v-test",
            )
        )
        db_session.add(
            SpotPriceForecast(
                site_id=site_id,
                timestamp=ts,
                prix_eur_mwh=80.0,
                date_generation=gen,
                source="RTE",
            )
        )
    db_session.flush()


def test_site_inconnu(db_session, cfg_test):
    with pytest.raises(SiteNotFoundError):
        run_optimization(
            db_session,
            site_id="inconnu",
            soe_actuel_kwh=100.0,
            cfg=cfg_test,
        )


def test_pipeline_happy_path(db_session, sample_site, cfg_test):
    debut = _debut_test()
    _remplir_forecasts(db_session, sample_site.site_id, debut, conso=30.0, pv=20.0)

    resultat = run_optimization(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        cfg=cfg_test,
    )
    assert resultat.statut == STATUT_OK
    assert len(resultat.pas_reponse) == cfg_test.nb_pas_reponse  # 96


def test_pipeline_leve_si_forecasts_majoritairement_manquants(db_session, sample_site, cfg_test):
    """Aucun forecast en base → > 50 % manquants → 503 côté API."""
    with pytest.raises(ForecastsMissingError):
        run_optimization(
            db_session,
            site_id=sample_site.site_id,
            soe_actuel_kwh=100.0,
            cfg=cfg_test,
        )


def test_pipeline_statut_corrective_si_derive_elevee(db_session, sample_site, cfg_test):
    """Une trajectoire précédente + SoC réel très éloigné → statut 'corrective'."""
    debut = _debut_test()
    _remplir_forecasts(db_session, sample_site.site_id, debut, conso=30.0, pv=20.0)

    # Première optimisation pour avoir une trajectoire précédente.
    run_optimization(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        cfg=cfg_test,
    )

    # Deuxième optimisation avec un SoC réel très différent du prévu.
    resultat = run_optimization(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=20.0,  # capacité 200 → dérive potentielle importante
        cfg=cfg_test,
    )
    # Le statut est corrective si la dérive dépasse le seuil (10 %).
    # L'ampleur exacte dépend de la 1re trajectoire, mais 80 kWh / 200 = 40 % → > seuil.
    assert resultat.statut == STATUT_CORRECTIVE
    assert resultat.derive_pct is not None and resultat.derive_pct > cfg_test.seuil_derive_pct
