# CLAUDE.md — Service d'Optimisation BESS

Ce fichier donne les instructions à Claude Code pour travailler dans ce repo.

## Rôle du service

Ce repo implémente le **Service d'Optimisation BESS** du SGE (Système de Gestion de l'Énergie)
de Tewa Solar. Il expose une API REST accessible depuis chaque contrôleur terrain (via VPN).

Son rôle : calculer la **trajectoire énergétique optimale** d'une batterie (BESS) sur un
horizon de 24 heures (96 pas de 15 min), à partir des prévisions de consommation, de
production PV et des prix spots disponibles en base PostgreSQL.

La base PostgreSQL est **partagée** avec le Service de Prévision — ce service est en
**lecture seule** sur toutes les tables existantes. Il écrit uniquement dans
`trajectoires_optimisees` et `trajectoire_pas`.

---

## Commandes essentielles

```bash
# Installer les dépendances
uv pip install -e ".[dev]"

# Lancer tous les tests
uv run pytest

# Lancer un test unique
uv run pytest tests/optimizer/test_solver.py::test_solve_basic_case

# Lancer les tests sans couverture (plus rapide)
uv run pytest --no-cov

# Lint et formatage
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Appliquer les migrations Alembic
uv run alembic upgrade head

# Lancer le service localement (nécessite une DB PostgreSQL)
uv run uvicorn optimizer.main:app --host 0.0.0.0 --port 8080 --reload

# Lancer avec Docker
docker compose up -d
docker compose logs -f optimizer-service
```

---

## Architecture

```
POST /api/v1/optimize
       │
       ▼
api/routes.py          ←── validation Pydantic de la requête
       │
       ▼
pipeline/optimize.py   ←── orchestration : lit DB, détecte dérive, lance solveur
       │
       ├── db/readers.py       ←── lecture PostgreSQL (forecasts, params site, prix)
       ├── optimizer/solver.py ←── formulation LP (CVXPY + HiGHS) + résolution
       └── db/writers.py       ←── écriture trajectoire (trajectoires_optimisees)
```

### Couches et responsabilités

| Couche | Modules | Rôle |
|--------|---------|------|
| **API** | `api/routes.py`, `api/schemas.py` | FastAPI — validation entrée/sortie, auth Bearer token |
| **Pipeline** | `pipeline/optimize.py` | Orchestration : lit DB → détecte dérive → résout → écrit |
| **Solveur** | `optimizer/solver.py` | Formulation LP et résolution (CVXPY + HiGHS) |
| **DB** | `db/readers.py`, `db/writers.py`, `db/session.py` | Accès PostgreSQL — lecture seule sauf `trajectoires_optimisees` |
| **Config** | `config.yaml` | Paramètres configurables (jamais de valeurs en dur dans le code) |

---

## Convention de signe — PRODUCTEUR

**Tout le service utilise la convention producteur sans exception.**

| Grandeur | Signe positif | Signe négatif |
|----------|--------------|---------------|
| `P_pdl` | Injection réseau (on fournit) | Soutirage réseau (on consomme) |
| `P_bess` | Décharge BESS (fournit de l'énergie sur le bus) | Charge BESS (absorbe de l'énergie) |
| `P_pv` | Production PV (toujours ≥ 0) | — |
| `P_conso` | Consommation du site (toujours ≥ 0) | — |

---

## Formulation mathématique du problème LP

### Horizon et pas de temps

- **Optimisation interne** : 48 h — 192 pas de 15 min, indexés `t = 0 … 191`
- **Réponse au contrôleur** : 24 h — les 96 premiers pas uniquement (`t = 0 … 95`)

Optimiser sur 48 h évite l'effet de bord d'horizon (le solveur "voit" le lendemain et
ne vide pas la batterie en fin de fenêtre). Les 24 dernières heures servent de lookahead.

### Variables de décision

Pour chaque pas `t` :

```
e_charge(t)    ≥ 0   # énergie absorbée par la batterie sur le pas (kWh)
e_decharge(t)  ≥ 0   # énergie restituée par la batterie sur le pas (kWh)
```

Séparer charge et décharge en deux variables positives permet de rester en LP pur
(pas de variable binaire). Le solveur n'a jamais intérêt à charger et décharger
simultanément si les prix sont cohérents.

Puissance nette BESS en convention producteur :
```
P_bess(t) = (e_decharge(t) - e_charge(t)) / 0.25   # positif = décharge (production)
```

### Dynamique du SoC

Rendement symétrique `η` par site (ex. 0.95) :

```
SoC(0)   = soc_actuel_kwh                              # condition initiale (reçue en POST)
SoC(t+1) = SoC(t) + e_charge(t) × η - e_decharge(t) / η
```

### Bilan de puissance au PDL — convention producteur

```
P_pdl(t) = P_pv(t) + P_bess(t) - P_conso(t)

         = P_pv(t) + (e_decharge(t) - e_charge(t)) / 0.25 - P_conso(t)
```

- `P_pdl(t) > 0` : injection nette au réseau
- `P_pdl(t) < 0` : soutirage net au réseau

### Fonction objectif

Minimiser le coût total sur l'horizon 48 h.
En convention producteur, le coût = ce qu'on paye au réseau = énergie soutirée × prix.

```
minimiser  Σ_{t=0}^{191}  (-P_pdl(t)) × prix_spot(t) × 0.25

         = Σ_{t=0}^{191}  (P_conso(t) - P_pv(t) - P_bess(t)) × prix_spot(t) × 0.25
```

- Quand `P_pdl(t) < 0` (soutirage) : terme positif → coût → le solveur cherche à l'éviter
- Quand `P_pdl(t) > 0` (injection) et `prix_spot(t) > 0` : terme négatif → revenu →
  le solveur est incité à injecter quand c'est rentable
- Les sites avec `p_max_injection_kw = 0` n'injectent jamais (contrainte ci-dessous)

### Contraintes

```
# 1. Bornes SoC
SoC_min_kwh  ≤  SoC(t)  ≤  SoC_max_kwh                           ∀ t

# 2. Puissance max BESS (charge et décharge séparées)
e_charge(t)   / 0.25  ≤  p_max_bess_kw                            ∀ t
e_decharge(t) / 0.25  ≤  p_max_bess_kw                            ∀ t

# 3. Bornes de puissance au PDL — convention producteur
-p_max_soutirage_kw  ≤  P_pdl(t)  ≤  p_max_injection_kw           ∀ t

#    Pour les sites sans injection : p_max_injection_kw = 0
#    → la contrainte haute devient P_pdl(t) ≤ 0

# 4. Puissance souscrite — contrainte dure en convention producteur
P_pdl(t)  ≥  -p_souscrite_kw                                       ∀ t

#    On ne soutire jamais plus que la puissance souscrite.
#    Si cette contrainte rend le problème infaisable (P_conso - P_pv - P_max_bess > p_souscrite),
#    on ajoute une variable slack ≥ 0 avec pénalité très élevée et on retourne statut "degraded".

# 5. Conversions SoC
SoC_min_kwh = capacite_bess_kwh × soc_min_pct / 100
SoC_max_kwh = capacite_bess_kwh × soc_max_pct / 100
```

---

## Gestion des prix spots manquants (avant 16h)

Les prix spots J+1 sont publiés par RTE vers 15h30. Avant cette heure, les timestamps
de demain ne sont pas encore en base.

**Stratégie de fallback (dans `db/readers.py`, transparent pour le pipeline) :**

1. Chercher le prix du **même timestamp il y a 7 jours** (même jour de la semaine)
2. Si absent : utiliser la **moyenne des 4 dernières semaines** sur ce créneau horaire
3. Si toujours absent : utiliser `prix_spot_defaut_eur_mwh` (configurable dans `config.yaml`, défaut 80.0)

La fonction `get_prix_spots(site_id, debut, fin)` retourne toujours un array complet de
192 valeurs avec une colonne `est_fallback: bool` pour chaque pas (utile pour les logs).

---

## Schéma de données

### Tables lues (PostgreSQL partagé avec le Service de Prévision)

| Table | Colonnes utilisées |
|-------|--------------------|
| `sites` | `site_id`, `capacite_bess_kwh`, `p_max_bess_kw`, `p_souscrite_kw`, `soc_min_pct`, `soc_max_pct`, `p_max_injection_kw`, `p_max_soutirage_kw`, `rendement_bess` |
| `forecasts_consommation` | `site_id`, `timestamp`, `puissance_kw` (le plus récent `date_generation`) |
| `forecasts_production_pv` | `site_id`, `timestamp`, `puissance_kw` (le plus récent `date_generation`) |
| `forecasts_prix_spot` | `site_id`, `timestamp`, `prix_eur_mwh` |
| `trajectoires_optimisees` | Lecture pour calcul de dérive (dernière trajectoire par site) |

### Nouvelles colonnes à ajouter à la table `sites` (migration Alembic requise)

| Colonne | Type | Défaut | Description |
|---------|------|--------|-------------|
| `p_max_injection_kw` | Float | 0.0 | Puissance max injectable (0 = injection interdite) |
| `p_max_soutirage_kw` | Float | égal à `p_souscrite_kw` | Puissance max soutirée au PDL |
| `rendement_bess` | Float | 0.95 | Rendement charge/décharge symétrique |

### Nouvelles tables écrites par ce service

```sql
CREATE TABLE trajectoires_optimisees (
    id                  SERIAL PRIMARY KEY,
    site_id             VARCHAR(64) NOT NULL REFERENCES sites(site_id),
    timestamp_calcul    TIMESTAMPTZ NOT NULL,
    soc_initial_kwh     FLOAT NOT NULL,
    statut              VARCHAR(16) NOT NULL,  -- 'ok' | 'corrective' | 'degraded' | 'error'
    message             TEXT,
    derive_pct          FLOAT,                 -- null si première trajectoire
    horizon_debut       TIMESTAMPTZ NOT NULL,
    horizon_fin         TIMESTAMPTZ NOT NULL,
    INDEX (site_id, timestamp_calcul DESC)
);

CREATE TABLE trajectoire_pas (
    id                  SERIAL PRIMARY KEY,
    trajectoire_id      INTEGER NOT NULL REFERENCES trajectoires_optimisees(id) ON DELETE CASCADE,
    timestamp           TIMESTAMPTZ NOT NULL,
    energie_kwh         FLOAT NOT NULL,       -- positif = décharge (convention producteur)
    soc_cible_kwh       FLOAT NOT NULL
);
```

---

## API REST

### POST /api/v1/optimize — calcul de trajectoire

**Requête :**
```json
{
  "site_id": "string",
  "soc_actuel_kwh": 150.0,
  "capacite_bess_kwh": 200.0,
  "timestamp_requete": "2026-04-18T10:00:00+02:00"
}
```

**Réponse :**
```json
{
  "site_id": "string",
  "timestamp_calcul": "2026-04-18T10:00:05+02:00",
  "horizon_debut": "2026-04-18T10:00:00+02:00",
  "trajectoire": [
    {
      "timestamp": "2026-04-18T10:00:00+02:00",
      "energie_kwh": 12.5,
      "soc_cible_kwh": 137.5
    }
    // ... 95 autres entrées (total 96 = 24h)
  ],
  "statut": "ok",
  "message": ""
}
```

`energie_kwh` est en **convention producteur** : positif = décharge BESS.

La trajectoire retournée couvre toujours exactement 24 h (96 pas).
L'optimisation interne sur 48 h est transparente pour l'appelant.

**Codes HTTP :**
- `200` — trajectoire calculée (statut `ok`, `corrective` ou `degraded`)
- `404` — site_id inconnu
- `422` — requête invalide (Pydantic)
- `503` — DB inaccessible ou forecasts manquants (> 50 % de l'horizon)

### GET /api/v1/health

Retourne l'état du service et des dépendances (DB, dernière trajectoire par site).

### GET /api/v1/sites/{site_id}/trajectory

Retourne la dernière trajectoire calculée pour ce site.

### GET /api/v1/sites/{site_id}/status

Retourne la dérive courante et la date du dernier calcul.

---

## Authentification

Header `Authorization: Bearer <api_key>`. Une clé API par site.
Variable d'environnement `SITE_API_KEYS` = JSON map `{"site_abc": "key_xxx"}`.
Retourner `403` si la clé ne correspond pas au `site_id` de la requête.

---

## Variables d'environnement requises

```
DATABASE_URL       # postgresql://user:password@host:5432/dbname
SITE_API_KEYS      # {"site_abc": "key_xxx", "site_def": "key_yyy"}
LOG_LEVEL          # INFO (défaut)
```

---

## Tests

- Miroir de chemin obligatoire : `optimizer/solver.py` → `tests/optimizer/test_solver.py`
- Les tests n'accèdent jamais à la DB ni aux APIs externes — tout est mocké.
- Les tests du solveur utilisent des données synthétiques simples pour vérifier que :
  - Les contraintes SoC sont respectées sur tous les pas
  - La convention producteur est cohérente (P_pdl = P_pv + P_bess - P_conso)
  - Un site avec `p_max_injection_kw = 0` ne produit jamais de P_pdl > 0
  - La dérive > 10 % produit bien un statut `"corrective"`
- Après chaque feature, `pytest` passe en entier avant de continuer.

---

## Philosophie générale

Mêmes règles que le Service de Prévision :

- Code simple et explicite. Un développeur junior doit comprendre chaque fonction sans
  contexte supplémentaire.
- Une fonction = une responsabilité.
- Français pour les noms de variables métier (`soc_kwh`, `puissance_kw`, `site_id`).
  Anglais pour la structure technique (classes, exceptions, méthodes).
- Type hints obligatoires sur toutes les fonctions publiques.
- Dataclasses ou Pydantic pour tous les objets échangés entre modules.
- `logging` standard — jamais de `print()`.
- Les erreurs d'un site ne bloquent jamais les autres.
- Aucune credential dans le code — variables d'environnement uniquement.
