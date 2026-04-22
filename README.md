# Service d'Optimisation BESS

Microservice REST du **Système de Gestion de l'Énergie**.
Il calcule en temps réel la **trajectoire de charge/décharge optimale** d'un système
de stockage batterie (BESS) sur 24 heures, en minimisant le coût d'échange avec le réseau.

---

## Contexte

Chaque site solaire est équipé d'un contrôleur terrain qui interroge ce service  
toutes les 15 minutes via VPN. En réponse, le service renvoie les 96 pas de puissance  
(15 min × 96 = 24 h) que le BESS doit suivre.

L'optimisation est réalisée sur un horizon interne de **48 heures** (192 pas) pour éviter  
les effets de bord de fin d'horizon — seules les 24 premières heures sont retournées au  
contrôleur. Le solveur utilise les prévisions de consommation, de production PV et les  
prix spot EPEX déposés en base par le service de prévision.

La base PostgreSQL est **partagée** avec le Service de Prévision ; ce service est
**en lecture seule** sur toutes les tables existantes et écrit uniquement dans
`trajectoires_optimisees` et `trajectoire_pas`.

---

## Architecture

```
POST /api/v1/optimize
       │
       ▼
api/routes.py          ←── validation Pydantic
       │
       ▼
pipeline/optimize.py   ←── orchestration : lit DB → dérive → solveur → écrit
       │
       ├── db/readers.py       ←── forecasts, params site, prix spot (+ fallback J-1)
       ├── optimizer/solver.py ←── formulation LP (CVXPY + HiGHS) + résolution
       └── db/writers.py       ←── écriture dans trajectoires_optimisees
```

### Pile technique

| Composant       | Technologie                                   |
| --------------- | --------------------------------------------- |
| API REST        | FastAPI + Uvicorn                             |
| Optimisation    | CVXPY 1.5 + solveur HiGHS                     |
| Base de données | PostgreSQL (SQLAlchemy 2.0)                   |
| Migrations      | Alembic                                       |
| Config secrets  | Variables d'environnement (Pydantic Settings) |
| Config métier   | `config.yaml`                                 |
| Tests           | pytest + SQLite en mémoire                    |
| Packaging       | uv + hatchling                                |
| Conteneur       | Docker (Python 3.11-slim)                     |

---

## Formulation mathématique (résumé)

Le solveur résout un **problème LP** (programmation linéaire) à chaque appel.

**Variables de décision** (par pas `t`, convention producteur) :

```
e_charge(t)    ≥ 0   # énergie absorbée par la batterie (kWh)
e_decharge(t)  ≥ 0   # énergie restituée par la batterie (kWh)
```

**Objectif** — minimiser le coût réseau sur 48 h :

```
min  Σ  (P_conso(t) - P_pv(t) - P_bess(t)) × prix_spot(t) × 0.25
```

**Contraintes principales** : SoC borné entre 0 et `capacite_bess_kwh`, puissance BESS,
injection/soutirage PDL, puissance souscrite. Si la contrainte de puissance souscrite rend
le problème infaisable (surcharge exceptionnelle), une variable slack est ajoutée et le
statut passe à `"degraded"`.

---

## Prérequis

- Python ≥ 3.11
- [uv](https://github.com/astral-sh/uv) (gestionnaire de paquets)
- PostgreSQL ≥ 14 accessible en réseau (partagé avec le Service de Prévision)
- Docker + Docker Compose (pour le déploiement conteneurisé)

---

## Installation locale

```bash
# Cloner le dépôt
git clone <url-du-repo>
cd optimizer

# Installer les dépendances (dev inclus)
uv pip install -e ".[dev]"

# Copier et adapter le fichier d'environnement
cp .env.example .env
# → éditer DATABASE_URL dans .env

# Appliquer les migrations (crée les tables trajectoires_*)
uv run alembic upgrade head

# Lancer le service en mode développement
uv run uvicorn optimizer.main:app --host 0.0.0.0 --port 8080 --reload
```

Le service est disponible sur `http://localhost:8080`.
La documentation interactive Swagger est accessible sur `http://localhost:8080/docs`.

---

## Variables d'environnement

Copier `.env.example` en `.env` et renseigner les valeurs :

| Variable       | Obligatoire | Description                                          |
| -------------- | ----------- | ---------------------------------------------------- |
| `DATABASE_URL` | Oui         | `postgresql://user:password@host:5432/dbname`        |
| `LOG_LEVEL`    | Non         | Niveau de log (`INFO` par défaut)                    |
| `CONFIG_PATH`  | Non         | Chemin vers `config.yaml` (`config.yaml` par défaut) |

---

## Configuration métier (`config.yaml`)

Les paramètres fonctionnels sont séparés des secrets dans `config.yaml` :

| Paramètre                   | Défaut      | Description                                      |
| --------------------------- | ----------- | ------------------------------------------------ |
| `seuil_derive_pct`          | `10.0`      | Seuil de dérive SoC (%) pour statut `corrective` |
| `slack_penalty_eur_par_kwh` | `1 000 000` | Pénalité violation puissance souscrite           |
| `horizon_interne_h`         | `48`        | Horizon d'optimisation interne                   |
| `horizon_reponse_h`         | `24`        | Horizon retourné au contrôleur                   |
| `pas_minutes`               | `15`        | Pas de temps (minutes)                           |

---

## Migrations de base de données

Ce service utilise **Alembic** avec sa propre table de versions
(`alembic_version_optimizer`) pour cohabiter avec le Service de Prévision.

```bash
# Appliquer toutes les migrations
uv run alembic upgrade head

# Vérifier l'état courant
uv run alembic current

# Revenir en arrière (rollback)
uv run alembic downgrade -1
```

La migration `0001_trajectoires` crée les deux tables :

- `trajectoires_optimisees` — métadonnées par calcul (statut, dérive, horizon)
- `trajectoire_pas` — 96 pas de 15 min par trajectoire

---

## Déploiement Docker

### Démarrage rapide

```bash
# Construire et lancer
docker compose -f docker/docker-compose.yml up -d

# Suivre les logs
docker compose -f docker/docker-compose.yml logs -f optimizer-service

# Arrêter
docker compose -f docker/docker-compose.yml down
```

Le service écoute sur `127.0.0.1:8080` (localhost uniquement — exposer via un reverse
proxy comme nginx pour l'accès VPN).

### Variables d'environnement en production

Le fichier `.env` est monté automatiquement par `docker-compose.yml`.
En production, préférer un gestionnaire de secrets (Vault, Docker Secrets, etc.)
plutôt qu'un fichier `.env` sur disque.

### Image Docker

L'image est construite depuis `docker/Dockerfile` (Python 3.11-slim) :

- Installe `libgomp1`, `libblas3`, `liblapack3` requis par HiGHS/CVXPY
- Utilise `uv` pour l'installation des dépendances
- Tourne en **utilisateur non-root** (UID 1000)
- Expose le port **8080**

---

## API REST

### `POST /api/v1/optimize`

Calcule la trajectoire optimale pour un site.

**Requête :**

```json
{
  "site_id": "site-01",
  "soe_actuel_kwh": 150.0,
  "capacite_bess_kwh": 200.0
}
```

```bash
curl -X POST http://127.0.0.1:8080/api/v1/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "site-demo-01",
    "soe_actuel_kwh": 150.0,
    "capacite_bess_kwh": 200.0
  }'
```

**Réponse :**

```json
{
  "site_id": "site-01",
  "timestamp_calcul": "2026-04-19T10:00:03+02:00",
  "horizon_debut": "2026-04-19T10:00:00+02:00",
  "trajectoire": [
    { "timestamp": "2026-04-19T10:00:00+02:00", "energie_kwh": 12.5,  "soe_cible_kwh": 137.5 },
    { "timestamp": "2026-04-19T10:15:00+02:00", "energie_kwh": -10.0, "soe_cible_kwh": 147.0 }
  ],
  "statut": "ok",
  "message": ""
}
```

`energie_kwh` en **convention producteur** : positif = décharge BESS, négatif = charge.

| Statut       | Signification                                                |
| ------------ | ------------------------------------------------------------ |
| `ok`         | Trajectoire optimale sans contrainte violée                  |
| `corrective` | Dérive SoC > seuil — trajectoire recalculée                  |
| `degraded`   | Puissance souscrite impossible à tenir (slack activé)        |
| `error`      | Erreur interne (DB inaccessible, forecasts manquants > 50 %) |

**Codes HTTP :**

| Code  | Cause                                                        |
| ----- | ------------------------------------------------------------ |
| `200` | Succès (`ok`, `corrective` ou `degraded`)                    |
| `404` | site_id inconnu                                              |
| `422` | Corps de requête invalide (Pydantic)                         |
| `503` | DB inaccessible ou forecasts manquants (> 50 % de l'horizon) |

### `GET /api/v1/health`

État du service et des dépendances (connectivité DB, nombre de trajectoires par site).

### `GET /api/v1/sites/{site_id}/trajectory`

Dernière trajectoire calculée pour ce site.

### `GET /api/v1/sites/{site_id}/status`

Dérive SoC courante et date du dernier calcul.

---

## Gestion des prix spots manquants

Les prix EPEX J+1 sont publiés par RTE vers 16h. Avant cette heure, la fenêtre de
48 h contient des créneaux sans prix en base. La stratégie dans `db/readers.py` :

1. Prix exact en base → utilisé directement.
2. Prix manquant → chercher le même créneau **J-1, J-2, J-3** (dans cet ordre, premier trouvé).
3. Aucun prix sur les 3 derniers jours → lève `PrixSpotsIndisponibles` → HTTP 503 `"prix spots non dispo"`.

Chaque pas en fallback est loggué avec `est_fallback=True`.

---

## Tests

Les tests n'accèdent jamais à la DB PostgreSQL ni aux APIs externes — tout est mocké
via une base SQLite en mémoire (`tests/conftest.py`).

```bash
# Tous les tests avec couverture
uv run pytest

# Sans couverture (plus rapide)
uv run pytest --no-cov

# Un test spécifique
uv run pytest tests/optimizer/test_solver.py::test_solve_basic_case -v

# Lint et formatage
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Les tests du solveur vérifient notamment :

- Le respect des bornes SoC sur tous les pas
- La cohérence de la convention producteur (`P_pdl = P_pv + P_bess - P_conso`)
- Qu'un site avec `p_max_injection_kw = 0` ne produit jamais `P_pdl > 0`
- Qu'une dérive > 10 % produit bien le statut `"corrective"`

---

## Structure du projet

```
src/optimizer/
├── main.py              # Entrée FastAPI
├── config.py            # Settings (.env) + ConfigYaml (config.yaml)
├── exceptions.py        # Exceptions métier → codes HTTP
├── api/
│   ├── routes.py        # Endpoints FastAPI
│   └── schemas.py       # Modèles Pydantic entrée/sortie
├── db/
│   ├── models.py        # ORM SQLAlchemy
│   ├── session.py       # Factory session DB
│   ├── readers.py       # Requêtes lecture (forecasts, prix, site)
│   └── writers.py       # Écriture trajectoires
├── optimizer/
│   ├── solver.py        # Formulation LP + résolution CVXPY/HiGHS
│   └── types.py         # Dataclasses internes (SiteParams, SolverInput/Output)
└── pipeline/
    ├── optimize.py      # Orchestration complète
    └── drift.py         # Calcul de dérive SoC
```
