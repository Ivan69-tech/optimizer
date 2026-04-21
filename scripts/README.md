# Scripts d'analyse BESS

## analyse_trajectoire.py

Outil d'analyse et de vérification visuelle des trajectoires calculées par le service d'optimisation BESS.

### Ce que fait le script

1. Appelle l'API du service d'optimisation (`POST /api/v1/optimize`) pour déclencher un calcul de trajectoire sur 24h.
2. Interroge la base PostgreSQL pour récupérer les prévisions de consommation, de production PV et les prix spots utilisés par l'optimiseur.
3. Génère un rapport HTML interactif avec :
   - **4 graphes synchronisés** (zoom sur l'un = zoom sur tous) :
     - Puissance BESS et bilan au PDL (+ SoE % en axe secondaire)
     - Prévision de consommation
     - Prévision de production PV
     - Prix spot (points en fallback mis en évidence)
   - **Tableau de métriques économiques** :
     - Coût de soutirage et revenus d'injection avec/sans BESS
     - Gain net en euros sur 24h
     - Taux d'autoconsommation (% de la production PV consommée localement)
     - Taux d'autosuffisance (% de la consommation couverte sans le réseau)
     - Énergie chargée / déchargée par la batterie

### Prérequis

Le service d'optimisation doit être accessible et la base PostgreSQL doit contenir des prévisions pour le site testé.

Installer les dépendances du script :

```bash
uv pip install -e ".[dev,scripts]"
```

### Configuration

Adapter le fichier `scripts/analyse_optimisation.yaml` :

```yaml
site_id: "site_abc"           # identifiant du site à analyser
soe_actuel_kwh: 100.0         # état de charge initial de la batterie (kWh)
capacite_bess_kwh: 200.0      # capacité totale de la batterie (kWh)

optimizer_url: "http://localhost:8080"   # URL du service d'optimisation

database_url: "postgresql://user:password@host:5432/dbname"  # accès DB
prix_spot_defaut_eur_mwh: 80.0           # prix de fallback si absent en DB (EUR/MWh)

output_html: "analyse_trajectoire.html"  # fichier de sortie
ouvrir_navigateur: true                  # ouvrir automatiquement dans le navigateur
```

### Lancement

```bash
# Depuis la racine du projet
uv run python scripts/analyse_trajectoire.py

# Avec un fichier de config spécifique
uv run python scripts/analyse_trajectoire.py --config chemin/vers/config.yaml
```

Le rapport HTML est généré dans le répertoire courant (chemin défini par `output_html`).

### Lire le rapport

Le rapport HTML s'ouvre dans n'importe quel navigateur. Il ne nécessite pas de serveur.

- **Zoom synchronisé** : zoomer ou déplacer la vue sur l'un des 4 graphes déplace automatiquement les 3 autres sur la même fenêtre temporelle.
- **Tooltip unifié** : survoler le graphe affiche les valeurs de toutes les courbes pour l'instant pointé.
- **Points orange (×)** sur le graphe des prix : prix calculés par fallback (J-7 ou moyenne 4 semaines), pas disponibles directement en base pour cet horizon.

### Interprétation des métriques

| Métrique | Description |
|----------|-------------|
| **Gain net (€)** | Économie réalisée sur la facture d'énergie sur 24h grâce à la BESS |
| **Taux d'autoconsommation** | % de la production PV consommée localement (directement ou via la batterie) — 100 % = rien n'est injecté |
| **Taux d'autosuffisance** | % de la consommation couverte sans soutirer au réseau — 100 % = autonomie totale |
| **Énergie chargée / déchargée** | Cycles effectués sur 24h (convention producteur : décharge > 0) |
