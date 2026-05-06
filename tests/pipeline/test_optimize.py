"""
Tests du pipeline : end-to-end (DB en mémoire + solveur réel) et unitaires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from optimizer.db.models import (
    ConsumptionForecast,
    PVProductionForecast,
    SpotPriceForecast,
    Trajectoire,
    TrajectoirePas,
)
from optimizer.exceptions import ForecastsMissingError, PrixSpotsIndisponibles, SiteNotFoundError
from optimizer.pipeline.optimize import (
    STATUT_CORRECTIVE,
    STATUT_OK,
    needs_recompute,
    prochain_quart_heure,
    run_optimization,
)

N = 192

# Temps fixe utilisé dans les tests unitaires de needs_recompute.
# 10:22 → prochain_quart_heure = 10:30
_NOW_TEST = datetime(2026, 5, 6, 10, 22, 0, tzinfo=UTC)


def _debut_test() -> datetime:
    """Retourne le prochain créneau de 15 min (même logique que le pipeline)."""
    return prochain_quart_heure(datetime.now(UTC))


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


# ---------------------------------------------------------------------------
# Tests end-to-end du pipeline
# ---------------------------------------------------------------------------


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


def test_pipeline_leve_si_prix_spots_indisponibles(db_session, sample_site, cfg_test):
    """Forecasts présents mais aucun prix en base → PrixSpotsIndisponibles."""
    debut = _debut_test()
    gen = datetime.now(UTC)
    for i in range(N):
        ts = debut + timedelta(minutes=15 * i)
        db_session.add(
            ConsumptionForecast(
                site_id=sample_site.site_id,
                timestamp=ts,
                puissance_kw=30.0,
                horizon_h=48,
                date_generation=gen,
                version_modele="v-test",
            )
        )
        db_session.add(
            PVProductionForecast(
                site_id=sample_site.site_id,
                timestamp=ts,
                puissance_kw=20.0,
                horizon_h=48,
                date_generation=gen,
                version_modele="v-test",
            )
        )
    db_session.flush()

    with pytest.raises(PrixSpotsIndisponibles):
        run_optimization(
            db_session,
            site_id=sample_site.site_id,
            soe_actuel_kwh=100.0,
            cfg=cfg_test,
        )


def test_pipeline_statut_corrective_si_derive_elevee(db_session, sample_site, cfg_test):
    """Trajectoire précédente avec pas dans le passé + SoC réel très éloigné → corrective."""
    now = datetime.now(UTC)

    # Simuler une trajectoire calculée il y a 30 min avec un pas passé.
    t_pas = now - timedelta(minutes=30)
    traj_ancienne = Trajectoire(
        site_id=sample_site.site_id,
        timestamp_calcul=t_pas,
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=t_pas,
        horizon_fin=t_pas + timedelta(hours=48),
    )
    db_session.add(traj_ancienne)
    db_session.add(
        TrajectoirePas(
            site_id=sample_site.site_id,
            timestamp=t_pas,
            energie_kwh=0.0,
            soe_cible_kwh=100.0,  # SoE prévu : 100 kWh
            insertion_timestamp=now,
        )
    )
    db_session.flush()

    debut = _debut_test()
    _remplir_forecasts(db_session, sample_site.site_id, debut, conso=30.0, pv=20.0)

    # soe_actuel=20 → dérive = |20-100|/200*100 = 40 % > seuil 10 %
    resultat = run_optimization(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=20.0,
        cfg=cfg_test,
    )
    assert resultat.statut == STATUT_CORRECTIVE
    assert resultat.derive_pct is not None and resultat.derive_pct > cfg_test.seuil_derive_pct


# ---------------------------------------------------------------------------
# Tests unitaires de prochain_quart_heure
# ---------------------------------------------------------------------------


def test_prochain_quart_heure_multiple_et_strict():
    """horizon_debut est un multiple de 15 min et strictement > now."""
    now = datetime(2026, 5, 6, 10, 22, 30, tzinfo=UTC)
    h = prochain_quart_heure(now)
    assert h.second == 0
    assert h.microsecond == 0
    assert h.minute % 15 == 0
    assert h > now


def test_prochain_quart_heure_sur_frontiere_exacte():
    """Sur un créneau exact (10:15:00), le prochain est 10:30 — strictement > now."""
    now = datetime(2026, 5, 6, 10, 15, 0, tzinfo=UTC)
    h = prochain_quart_heure(now)
    assert h == datetime(2026, 5, 6, 10, 30, 0, tzinfo=UTC)
    assert h > now


# ---------------------------------------------------------------------------
# Tests unitaires de needs_recompute
# ---------------------------------------------------------------------------


def _creer_trajectoire(
    db_session,
    site_id: str,
    timestamp_calcul: datetime,
    date_generation_forecasts: datetime | None = None,
) -> Trajectoire:
    traj = Trajectoire(
        site_id=site_id,
        timestamp_calcul=timestamp_calcul,
        soe_initial_kwh=100.0,
        statut="ok",
        message=None,
        derive_pct=None,
        horizon_debut=timestamp_calcul,
        horizon_fin=timestamp_calcul + timedelta(hours=48),
        date_generation_forecasts=date_generation_forecasts,
    )
    db_session.add(traj)
    db_session.flush()
    return traj


def test_needs_recompute_cache_vide(db_session, sample_site, cfg_test):
    """Aucune trajectoire précédente → doit recalculer."""
    result, derive = needs_recompute(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
        derniere_trajectoire=None,
        now=_NOW_TEST,
        cfg=cfg_test,
    )
    assert result is True
    assert derive is None


def test_needs_recompute_derive_elevee(db_session, sample_site, cfg_test):
    """Dérive interpolée > seuil → doit recalculer."""
    # Pas à 10:15 avec soe=100. now=10:22 → pas_apres absent → soe_attendu=100.
    # soe_actuel=200 → dérive=50% > seuil 10%.
    t_pas = datetime(2026, 5, 6, 10, 15, 0, tzinfo=UTC)
    traj = _creer_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=_NOW_TEST - timedelta(minutes=30),
    )
    db_session.add(
        TrajectoirePas(
            site_id=sample_site.site_id,
            timestamp=t_pas,
            energie_kwh=0.0,
            soe_cible_kwh=100.0,
            insertion_timestamp=_NOW_TEST,
        )
    )
    db_session.flush()

    result, derive = needs_recompute(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=200.0,
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
        derniere_trajectoire=traj,
        now=_NOW_TEST,
        cfg=cfg_test,
    )
    assert result is True
    assert derive is not None and derive > cfg_test.seuil_derive_pct


def test_needs_recompute_nouveaux_forecasts(db_session, sample_site, cfg_test):
    """Forecasts plus récents que date_generation_forecasts stockée → doit recalculer."""
    # stored = 08:00, nouveau max(date_generation) en DB = 09:00 → plus récent.
    date_stockee = datetime(2026, 5, 6, 8, 0, 0, tzinfo=UTC)
    date_nouvelle = datetime(2026, 5, 6, 9, 0, 0, tzinfo=UTC)
    traj = _creer_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=_NOW_TEST - timedelta(minutes=30),
        date_generation_forecasts=date_stockee,
    )

    # Insérer un forecast dans la fenêtre [10:30, +48h) avec la nouvelle date_generation.
    # prochain_quart_heure(_NOW_TEST) = 10:30
    ts_forecast = datetime(2026, 5, 6, 10, 30, 0, tzinfo=UTC)
    db_session.add(
        ConsumptionForecast(
            site_id=sample_site.site_id,
            timestamp=ts_forecast,
            puissance_kw=30.0,
            horizon_h=48,
            date_generation=date_nouvelle,
            version_modele="v-test",
        )
    )
    db_session.flush()

    result, derive = needs_recompute(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
        derniere_trajectoire=traj,
        now=_NOW_TEST,
        cfg=cfg_test,
    )
    assert result is True


def test_needs_recompute_trajectoire_trop_ancienne(db_session, sample_site, cfg_test):
    """Trajectoire calculée il y a > 4 h → doit recalculer."""
    traj = _creer_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=_NOW_TEST - timedelta(hours=5),
    )

    result, _ = needs_recompute(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
        derniere_trajectoire=traj,
        now=_NOW_TEST,
        cfg=cfg_test,
    )
    assert result is True


def test_needs_recompute_cache_valide(db_session, sample_site, cfg_test):
    """Aucun déclencheur actif → cache valide, pas de recalcul."""
    # Trajectoire récente (30 min) sans nouveaux forecasts et sans dérive.
    traj = _creer_trajectoire(
        db_session,
        site_id=sample_site.site_id,
        timestamp_calcul=_NOW_TEST - timedelta(minutes=30),
    )
    # Pas dans le passé avec soe=100 → dérive=0% si soe_actuel=100.
    t_pas = datetime(2026, 5, 6, 10, 15, 0, tzinfo=UTC)
    db_session.add(
        TrajectoirePas(
            site_id=sample_site.site_id,
            timestamp=t_pas,
            energie_kwh=0.0,
            soe_cible_kwh=100.0,
            insertion_timestamp=_NOW_TEST,
        )
    )
    db_session.flush()
    # Pas de forecasts dans la fenêtre → max_date_gen=None → cas 3 ignoré.

    result, derive = needs_recompute(
        db_session,
        site_id=sample_site.site_id,
        soe_actuel_kwh=100.0,
        capacite_bess_kwh=sample_site.capacite_bess_kwh,
        derniere_trajectoire=traj,
        now=_NOW_TEST,
        cfg=cfg_test,
    )
    assert result is False
    assert derive is not None and derive < cfg_test.seuil_derive_pct
