"""
Configuration du service : variables d'environnement + fichier YAML.

- `settings` (pydantic-settings) : lit `.env` — secrets et infra.
- `load_config_yaml()` : lit le fichier pointé par `CONFIG_PATH` — paramètres fonctionnels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Base de données
    database_url: str = "postgresql://optimizer:optimizer@localhost:5432/optimizer"

    # Authentification API — JSON map {site_id: api_key}
    site_api_keys: str = "{}"

    # Chemin du fichier de configuration fonctionnelle
    config_path: Path = Path("config.yaml")

    # Logging
    log_level: str = "INFO"

    def parsed_api_keys(self) -> dict[str, str]:
        """Parse SITE_API_KEYS (JSON) en dict Python."""
        try:
            data = json.loads(self.site_api_keys)
        except json.JSONDecodeError as err:
            raise ValueError(f"SITE_API_KEYS n'est pas un JSON valide : {err}") from err
        if not isinstance(data, dict):
            raise ValueError("SITE_API_KEYS doit être un objet JSON {site_id: key}.")
        return {str(k): str(v) for k, v in data.items()}


settings = Settings()


@dataclass(frozen=True)
class ConfigYaml:
    """Paramètres fonctionnels lus depuis config.yaml."""

    prix_spot_defaut_eur_mwh: float
    seuil_derive_pct: float
    slack_penalty_eur_par_kwh: float
    seuil_slack_kwh: float
    horizon_interne_h: int
    horizon_reponse_h: int
    pas_minutes: int

    @property
    def nb_pas_interne(self) -> int:
        """Nombre de pas de l'optimisation interne (ex. 192 = 48 h × 4)."""
        return self.horizon_interne_h * 60 // self.pas_minutes

    @property
    def nb_pas_reponse(self) -> int:
        """Nombre de pas retournés au contrôleur (ex. 96 = 24 h × 4)."""
        return self.horizon_reponse_h * 60 // self.pas_minutes

    @property
    def pas_heure(self) -> float:
        """Durée d'un pas en heures (ex. 0.25)."""
        return self.pas_minutes / 60.0


def load_config_yaml(path: Path | None = None) -> ConfigYaml:
    """Charge les paramètres fonctionnels depuis un fichier YAML."""
    chemin = path or settings.config_path
    with open(chemin, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ConfigYaml(
        prix_spot_defaut_eur_mwh=float(data["prix_spot_defaut_eur_mwh"]),
        seuil_derive_pct=float(data["seuil_derive_pct"]),
        slack_penalty_eur_par_kwh=float(data["slack_penalty_eur_par_kwh"]),
        seuil_slack_kwh=float(data["seuil_slack_kwh"]),
        horizon_interne_h=int(data["horizon_interne_h"]),
        horizon_reponse_h=int(data["horizon_reponse_h"]),
        pas_minutes=int(data["pas_minutes"]),
    )
