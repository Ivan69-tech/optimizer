"""
Modèles ORM SQLAlchemy pour le service d'optimisation.

Ce service partage la base PostgreSQL avec le service de prévision :
- Lecture seule : `sites`, `forecasts_consommation`, `forecasts_production_pv`,
  `forecasts_prix_spot`.
- Lecture/écriture : `trajectoires_optimisees`, `trajectoire_pas` (créées par ce service).

Les colonnes `p_max_injection_kw`, `p_max_soutirage_kw` et `rendement_bess` sont
ajoutées à la table `sites` par le service de prévision via une migration dédiée ;
ce service les lit uniquement.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Site(Base):
    """Table `sites` — paramètres techniques par site (lecture seule)."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    nom: Mapped[str] = mapped_column(String(128), nullable=False)
    capacite_bess_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    p_max_bess_kw: Mapped[float] = mapped_column(Float, nullable=False)
    p_souscrite_kw: Mapped[float] = mapped_column(Float, nullable=False)

    # Colonnes ajoutées par le forecaster — lues uniquement par ce service.
    # Defaults fournis pour robustesse si la migration forecaster n'est pas encore
    # appliquée (dev local, tests SQLite).
    p_max_injection_kw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p_max_soutirage_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rendement_bess: Mapped[float] = mapped_column(Float, nullable=False, default=0.95)


class ConsumptionForecast(Base):
    """Table `forecasts_consommation` — prévisions conso (lecture seule)."""

    __tablename__ = "forecasts_consommation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sites.site_id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    puissance_kw: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_h: Mapped[int] = mapped_column(Integer, nullable=False)
    date_generation: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_modele: Mapped[str] = mapped_column(String(32), nullable=False)


class PVProductionForecast(Base):
    """Table `forecasts_production_pv` — prévisions PV (lecture seule)."""

    __tablename__ = "forecasts_production_pv"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sites.site_id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    puissance_kw: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_h: Mapped[int] = mapped_column(Integer, nullable=False)
    date_generation: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_modele: Mapped[str] = mapped_column(String(32), nullable=False)


class SpotPriceForecast(Base):
    """Table `forecasts_prix_spot` — prix spots RTE (lecture seule)."""

    __tablename__ = "forecasts_prix_spot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sites.site_id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    prix_eur_mwh: Mapped[float] = mapped_column(Float, nullable=False)
    date_generation: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="RTE")


class Trajectoire(Base):
    """Table `trajectoires_optimisees` — métadonnées d'une trajectoire calculée."""

    __tablename__ = "trajectoires_optimisees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String(64), ForeignKey("sites.site_id"), nullable=False)
    timestamp_calcul: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    soe_initial_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    derive_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_debut: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)



class TrajectoirePas(Base):
    """Table `trajectoire_pas` — table glissante : une ligne par (site_id, timestamp).

    À chaque recalcul, les pas futurs (timestamp >= horizon_debut) sont supprimés puis
    réinsérés. Les pas passés sont conservés indéfiniment.
    """

    __tablename__ = "trajectoire_pas"

    site_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sites.site_id"), nullable=False, primary_key=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    energie_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    soe_cible_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    insertion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
