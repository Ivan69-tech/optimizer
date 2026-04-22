"""
Script d'analyse et de vérification visuelle des trajectoires BESS.

Appelle l'API optimizer, récupère les prévisions en DB, génère un rapport
HTML interactif avec 4 graphes synchronisés et des métriques économiques.

Usage :
    uv run python scripts/analyse_trajectoire.py [--config scripts/analyse_optimisation.yaml]
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import yaml
from plotly.subplots import make_subplots
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from optimizer.db.readers import (
    get_forecast_consommation,
    get_forecast_production_pv,
    get_prix_spots,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAS_H = 0.25  # 15 minutes en heures


@dataclass(frozen=True)
class AnalyseConfig:
    site_id: str
    soe_actuel_kwh: float
    capacite_bess_kwh: float
    optimizer_url: str
    database_url: str
    prix_spot_defaut_eur_mwh: float
    output_html: str
    ouvrir_navigateur: bool


@dataclass(frozen=True)
class MetriquesCalculees:
    cout_sans_bess: float
    revenu_sans_bess: float
    cout_net_sans_bess: float
    cout_avec_bess: float
    revenu_avec_bess: float
    cout_net_avec_bess: float
    gain_eur: float
    energie_pv_kwh: float
    energie_injection_kwh: float
    taux_autoconsom_pct: float
    energie_conso_kwh: float
    energie_soutirage_kwh: float
    taux_autosuffisance_pct: float
    energie_chargee_kwh: float
    energie_dechargee_kwh: float


def charger_config(path: str | Path) -> AnalyseConfig:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Fichier de configuration introuvable : {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        sys.exit(f"Erreur de parsing YAML : {exc}")

    champs_requis = [
        "site_id",
        "soe_actuel_kwh",
        "capacite_bess_kwh",
        "optimizer_url",
        "database_url",
        "prix_spot_defaut_eur_mwh",
        "output_html",
        "ouvrir_navigateur",
    ]
    for champ in champs_requis:
        if champ not in raw:
            sys.exit(f"Clé manquante dans la configuration : '{champ}'")

    return AnalyseConfig(
        site_id=str(raw["site_id"]),
        soe_actuel_kwh=float(raw["soe_actuel_kwh"]),
        capacite_bess_kwh=float(raw["capacite_bess_kwh"]),
        optimizer_url=str(raw["optimizer_url"]).rstrip("/"),
        database_url=str(raw["database_url"]),
        prix_spot_defaut_eur_mwh=float(raw["prix_spot_defaut_eur_mwh"]),
        output_html=str(raw["output_html"]),
        ouvrir_navigateur=bool(raw["ouvrir_navigateur"]),
    )


def appeler_api_optimizer(cfg: AnalyseConfig) -> dict:
    url = f"{cfg.optimizer_url}/api/v1/optimize"
    payload = {
        "site_id": cfg.site_id,
        "soe_actuel_kwh": cfg.soe_actuel_kwh,
        "capacite_bess_kwh": cfg.capacite_bess_kwh,
    }
    logger.info("POST %s — site=%s", url, cfg.site_id)
    try:
        resp = requests.post(url, json=payload, timeout=30)
    except requests.ConnectionError:
        sys.exit(
            f"Impossible de joindre l'optimizer ({url}).\n"
            "Vérifiez que le service est démarré et que optimizer_url est correct."
        )
    except requests.Timeout:
        sys.exit(f"Timeout après 30s sur {url}.")

    if resp.status_code != 200:
        sys.exit(
            f"Erreur API HTTP {resp.status_code} :\n{resp.text}\n"
            "Vérifiez site_id et que les forecasts sont en base."
        )

    data = resp.json()
    logger.info(
        "Réponse reçue : statut=%s | horizon=%s → %s",
        data.get("statut"),
        data.get("horizon_debut"),
        data.get("horizon_fin", "N/A"),
    )
    return data


def construire_timestamps(horizon_debut: datetime, n: int) -> list[datetime]:
    return [horizon_debut + timedelta(minutes=15 * i) for i in range(n)]


def _creer_session(database_url: str):
    if database_url.startswith("sqlite"):
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(
            database_url,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
        )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory()


def charger_forecasts(cfg: AnalyseConfig, timestamps: list[datetime]) -> pd.DataFrame:
    session = _creer_session(cfg.database_url)
    try:
        debut = timestamps[0]
        fin = timestamps[-1] + timedelta(minutes=15)

        conso = get_forecast_consommation(session, cfg.site_id, debut, fin, timestamps)
        pv = get_forecast_production_pv(session, cfg.site_id, debut, fin, timestamps)
        prix = get_prix_spots(session, cfg.site_id, timestamps, cfg.prix_spot_defaut_eur_mwh)
    except Exception as exc:
        sys.exit(
            f"Erreur de lecture des forecasts en base : {exc}\n"
            "Vérifiez database_url et que le site_id existe en base."
        )
    finally:
        session.close()

    nb_conso_fallback = sum(1 for p in conso if p.est_fallback)
    nb_pv_fallback = sum(1 for p in pv if p.est_fallback)
    nb_prix_fallback = sum(1 for p in prix if p.est_fallback)

    if nb_conso_fallback == len(conso):
        logger.warning("Aucune prévision de consommation en base — toutes les valeurs sont à 0.")
    if nb_pv_fallback == len(pv):
        logger.warning("Aucune prévision PV en base — toutes les valeurs sont à 0.")
    if nb_prix_fallback > 0:
        logger.info("%d/%d prix spots en fallback.", nb_prix_fallback, len(prix))

    df = pd.DataFrame(
        {
            "timestamp": [p.timestamp for p in conso],
            "conso_kw": [p.valeur for p in conso],
            "conso_fallback": [p.est_fallback for p in conso],
            "pv_kw": [p.valeur for p in pv],
            "pv_fallback": [p.est_fallback for p in pv],
            "prix_eur_mwh": [p.valeur for p in prix],
            "prix_fallback": [p.est_fallback for p in prix],
        }
    )
    # Normaliser les timestamps en tz-naïf pour le merge avec la trajectoire API
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.set_index("timestamp")
    return df


def fusionner_trajectoire(
    df_forecasts: pd.DataFrame,
    reponse: dict,
    cfg: AnalyseConfig,
) -> pd.DataFrame:
    traj = reponse["trajectoire"]
    n_pas = len(traj)

    df_traj = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([p["timestamp"] for p in traj]).tz_localize(None),
            "energie_kwh": [p["energie_kwh"] for p in traj],
            "soe_cible_kwh": [p["soe_cible_kwh"] for p in traj],
        }
    ).set_index("timestamp")

    df = df_forecasts.join(df_traj, how="inner")
    if len(df) != n_pas:
        sys.exit(
            f"Impossible d'aligner trajectoire et forecasts : {len(df)} lignes communes "
            f"({n_pas} attendues). Vérifiez que les timestamps sont cohérents."
        )

    df["p_bess_kw"] = df["energie_kwh"] / PAS_H
    df["p_pdl_avec_bess_kw"] = df["pv_kw"] + df["p_bess_kw"] - df["conso_kw"]
    df["p_pdl_sans_bess_kw"] = df["pv_kw"] - df["conso_kw"]
    df["soe_pct"] = df["soe_cible_kwh"] / cfg.capacite_bess_kwh * 100
    return df


def calculer_metriques(df: pd.DataFrame) -> MetriquesCalculees:
    p_sans = df["p_pdl_sans_bess_kw"]
    p_avec = df["p_pdl_avec_bess_kw"]
    prix = df["prix_eur_mwh"]

    cout_sans = ((-p_sans).clip(lower=0) * prix * PAS_H / 1000).sum()
    revenu_sans = (p_sans.clip(lower=0) * prix.clip(lower=0) * PAS_H / 1000).sum()
    cout_avec = ((-p_avec).clip(lower=0) * prix * PAS_H / 1000).sum()
    revenu_avec = (p_avec.clip(lower=0) * prix.clip(lower=0) * PAS_H / 1000).sum()

    energie_pv_kwh = (df["pv_kw"] * PAS_H).sum()
    energie_injection_kwh = (p_avec.clip(lower=0) * PAS_H).sum()
    taux_autoconsom_pct = (1 - energie_injection_kwh / max(energie_pv_kwh, 1e-6)) * 100

    energie_conso_kwh = (df["conso_kw"] * PAS_H).sum()
    energie_soutirage_kwh = ((-p_avec).clip(lower=0) * PAS_H).sum()
    taux_autosuffisance_pct = (1 - energie_soutirage_kwh / max(energie_conso_kwh, 1e-6)) * 100

    energie_chargee_kwh = (-df["energie_kwh"]).clip(lower=0).sum()
    energie_dechargee_kwh = df["energie_kwh"].clip(lower=0).sum()

    return MetriquesCalculees(
        cout_sans_bess=cout_sans,
        revenu_sans_bess=revenu_sans,
        cout_net_sans_bess=cout_sans - revenu_sans,
        cout_avec_bess=cout_avec,
        revenu_avec_bess=revenu_avec,
        cout_net_avec_bess=cout_avec - revenu_avec,
        gain_eur=(cout_sans - revenu_sans) - (cout_avec - revenu_avec),
        energie_pv_kwh=energie_pv_kwh,
        energie_injection_kwh=energie_injection_kwh,
        taux_autoconsom_pct=taux_autoconsom_pct,
        energie_conso_kwh=energie_conso_kwh,
        energie_soutirage_kwh=energie_soutirage_kwh,
        taux_autosuffisance_pct=taux_autosuffisance_pct,
        energie_chargee_kwh=energie_chargee_kwh,
        energie_dechargee_kwh=energie_dechargee_kwh,
    )


def construire_figure(df: pd.DataFrame, cfg: AnalyseConfig) -> go.Figure:
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            "Bilan BESS + PDL",
            "Prix spot (EUR/MWh)",
            "Consommation (kW)",
            "Production PV (kW)",
        ),
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
        ],
    )

    x = df.index

    # --- Ligne 1 : P_bess, P_pdl, SoE ---
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["p_bess_kw"],
            name="P_bess (kW)",
            fill="tozeroy",
            mode="lines",
            line={"color": "steelblue", "width": 1.5, "shape": "hv"},
            fillcolor="rgba(70,130,180,0.25)",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["p_pdl_avec_bess_kw"],
            name="P_pdl avec BESS (kW)",
            mode="lines",
            line={"color": "darkorange", "width": 2, "shape": "hv"},
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    # soe_cible_kwh est l'état à la FIN de l'intervalle t (= début de t+1)
    # on décale de +15 min pour l'aligner avec la puissance qui cause ce changement
    x_soe = x + pd.Timedelta(minutes=15)
    fig.add_trace(
        go.Scatter(
            x=x_soe,
            y=df["soe_pct"],
            name="SoE (%)",
            mode="lines",
            line={"color": "green", "width": 1.5, "dash": "dot"},
        ),
        row=1,
        col=1,
        secondary_y=True,
    )

    # --- Ligne 2 : Prix spot (réels et fallback) ---
    mask_ok = ~df["prix_fallback"]
    mask_fb = df["prix_fallback"]

    if mask_ok.any():
        fig.add_trace(
            go.Scatter(
                x=x[mask_ok],
                y=df.loc[mask_ok, "prix_eur_mwh"],
                name="Prix spot (EUR/MWh)",
                mode="lines+markers",
                line={"color": "slategray", "width": 1.5, "shape": "hv"},
                marker={"size": 4, "color": "slategray"},
            ),
            row=2,
            col=1,
        )
    if mask_fb.any():
        fig.add_trace(
            go.Scatter(
                x=x[mask_fb],
                y=df.loc[mask_fb, "prix_eur_mwh"],
                name="Prix fallback",
                mode="markers",
                marker={"size": 8, "color": "orange", "symbol": "x"},
            ),
            row=2,
            col=1,
        )

    # --- Ligne 3 : Consommation ---
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["conso_kw"],
            name="Consommation (kW)",
            fill="tozeroy",
            mode="lines",
            line={"color": "crimson", "width": 1.5},
            fillcolor="rgba(220,20,60,0.2)",
        ),
        row=3,
        col=1,
    )

    # --- Ligne 4 : Production PV ---
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["pv_kw"],
            name="Production PV (kW)",
            fill="tozeroy",
            mode="lines",
            line={"color": "goldenrod", "width": 1.5},
            fillcolor="rgba(218,165,32,0.25)",
        ),
        row=4,
        col=1,
    )

    # Axes Y
    fig.update_yaxes(title_text="Puissance (kW)", secondary_y=False, row=1, col=1)
    fig.update_yaxes(title_text="SoE (%)", secondary_y=True, row=1, col=1, range=[0, 110])
    fig.update_yaxes(title_text="EUR/MWh", row=2, col=1)
    fig.update_yaxes(title_text="kW", row=3, col=1)
    fig.update_yaxes(title_text="kW", row=4, col=1)

    # Date + heure sur l'axe X de chaque sous-graphe
    fig.update_xaxes(
        showticklabels=True,
        tickformat="%H:%M\n%d/%m",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#888",
        spikedash="dot",
        spikethickness=1,
    )

    fig.update_layout(
        height=1200,
        title_text=f"Analyse trajectoire BESS — {cfg.site_id}",
        hovermode="x unified",
        hoversubplots="axis",  # hover commun à tous les sous-graphes (Plotly >= 5.17)
        legend={"orientation": "h", "y": -0.06, "x": 0},
        margin={"t": 80, "b": 80},
    )
    return fig


def construire_tableau_metriques_html(
    m: MetriquesCalculees,
    reponse: dict,
    cfg: AnalyseConfig,
    n_pas: int,
) -> str:
    statut = reponse.get("statut", "")
    message = reponse.get("message", "")
    timestamp_calcul = reponse.get("timestamp_calcul", "")
    horizon_debut = reponse.get("horizon_debut", "")

    badge_class = {
        "ok": "badge-ok",
        "corrective": "badge-corrective",
        "degraded": "badge-degraded",
    }.get(statut, "badge-ok")

    duree_h = int(n_pas * PAS_H)
    gain_color = "#27ae60" if m.gain_eur >= 0 else "#e74c3c"

    def eur(v: float) -> str:
        return f"{v:.2f} €"

    def kwh(v: float) -> str:
        return f"{v:.1f} kWh"

    def pct(v: float) -> str:
        return f"{v:.1f} %"

    return f"""
<div style="font-family:sans-serif; max-width:1400px; margin:0 auto 1rem; padding:0 1rem;">
  <h2 style="margin-bottom:0.3rem;">Analyse trajectoire BESS — {cfg.site_id}</h2>
  <p style="color:#555; margin-top:0;">
    Calcul&nbsp;: {timestamp_calcul} &nbsp;|&nbsp;
    Horizon&nbsp;: {horizon_debut} &nbsp;|&nbsp;
    Statut&nbsp;: <span class="{badge_class}">{statut.upper()}</span>
    {f"&nbsp;— {message}" if message else ""}
  </p>

  <table class="metriques-table" style="margin-bottom:1rem;">
    <thead>
      <tr><th>Indicateur financier ({duree_h}h)</th><th>Sans BESS</th><th>Avec BESS</th><th>Delta</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>Coût de soutirage</td>
        <td>{eur(m.cout_sans_bess)}</td>
        <td>{eur(m.cout_avec_bess)}</td>
        <td>{eur(m.cout_avec_bess - m.cout_sans_bess)}</td>
      </tr>
      <tr>
        <td>Revenus d'injection</td>
        <td>{eur(m.revenu_sans_bess)}</td>
        <td>{eur(m.revenu_avec_bess)}</td>
        <td>{eur(m.revenu_avec_bess - m.revenu_sans_bess)}</td>
      </tr>
      <tr style="font-weight:bold; background:#f0f0f0;">
        <td>Coût net</td>
        <td>{eur(m.cout_net_sans_bess)}</td>
        <td>{eur(m.cout_net_avec_bess)}</td>
        <td style="color:{gain_color};">{"+" if m.gain_eur >= 0 else ""}{eur(m.gain_eur)} ({"économie" if m.gain_eur >= 0 else "surcoût"})</td>
      </tr>
    </tbody>
  </table>

  <table class="metriques-table">
    <thead>
      <tr><th>Indicateur BESS / énergie</th><th>Valeur</th></tr>
    </thead>
    <tbody>
      <tr><td>Énergie chargée</td><td>{kwh(m.energie_chargee_kwh)}</td></tr>
      <tr><td>Énergie déchargée</td><td>{kwh(m.energie_dechargee_kwh)}</td></tr>
      <tr><td>Production PV totale</td><td>{kwh(m.energie_pv_kwh)}</td></tr>
      <tr><td>Injection réseau avec BESS</td><td>{kwh(m.energie_injection_kwh)}</td></tr>
      <tr><td>Soutirage réseau avec BESS</td><td>{kwh(m.energie_soutirage_kwh)}</td></tr>
      <tr><td>Taux d'autoconsommation</td><td>{pct(m.taux_autoconsom_pct)}</td></tr>
      <tr><td>Taux d'autosuffisance</td><td>{pct(m.taux_autosuffisance_pct)}</td></tr>
    </tbody>
  </table>
</div>
"""


_CSS = """
<style>
  body { font-family: sans-serif; max-width: 1400px; margin: auto; padding: 1rem; }
  .metriques-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  .metriques-table th { background: #2c3e50; color: white; padding: 8px 12px; text-align: left; }
  .metriques-table td { padding: 6px 12px; border-bottom: 1px solid #ddd; }
  .metriques-table tr:hover { background: #f9f9f9; }
  .badge-ok { color: #27ae60; font-weight: bold; }
  .badge-degraded { color: #e74c3c; font-weight: bold; }
  .badge-corrective { color: #e67e22; font-weight: bold; }
</style>
"""


def sauvegarder_html(
    fig: go.Figure,
    m: MetriquesCalculees,
    reponse: dict,
    cfg: AnalyseConfig,
    n_pas: int,
) -> Path:
    plot_div = fig.to_html(full_html=False, include_plotlyjs="cdn")
    tableau_html = construire_tableau_metriques_html(m, reponse, cfg, n_pas)

    page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Analyse BESS — {cfg.site_id}</title>
  {_CSS}
</head>
<body>
{tableau_html}
{plot_div}
</body>
</html>"""

    output_path = Path(cfg.output_html)
    try:
        output_path.write_text(page, encoding="utf-8")
    except OSError as exc:
        sys.exit(f"Impossible d'écrire le fichier HTML ({output_path}) : {exc}")

    logger.info("Rapport sauvegardé : %s", output_path.resolve())
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère un rapport HTML d'analyse de trajectoire BESS."
    )
    parser.add_argument(
        "--config",
        default="scripts/analyse_optimisation.yaml",
        help="Chemin vers le fichier de configuration YAML (défaut : scripts/analyse_optimisation.yaml)",
    )
    args = parser.parse_args()

    cfg = charger_config(args.config)
    logger.info("Configuration chargée : site=%s | soe=%.1f kWh", cfg.site_id, cfg.soe_actuel_kwh)

    reponse = appeler_api_optimizer(cfg)

    n_pas = len(reponse["trajectoire"])
    horizon_debut = datetime.fromisoformat(reponse["horizon_debut"])
    # Normaliser en tz-naïf pour construire les timestamps de requête DB
    if horizon_debut.tzinfo is not None:
        horizon_debut = horizon_debut.replace(tzinfo=None)
    timestamps = construire_timestamps(horizon_debut, n_pas)

    df_forecasts = charger_forecasts(cfg, timestamps)
    df = fusionner_trajectoire(df_forecasts, reponse, cfg)
    metriques = calculer_metriques(df)

    logger.info(
        "Métriques : gain=%.2f € | autoconsom=%.1f %% | autosuffis=%.1f %%",
        metriques.gain_eur,
        metriques.taux_autoconsom_pct,
        metriques.taux_autosuffisance_pct,
    )

    fig = construire_figure(df, cfg)
    output_path = sauvegarder_html(fig, metriques, reponse, cfg, n_pas)

    if cfg.ouvrir_navigateur:
        webbrowser.open(output_path.resolve().as_uri())


if __name__ == "__main__":
    main()
