# NetSentinel — Rapport technique : phase streaming
**Big Data – BLOC 2 - UE28**

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*

---

## Ce que fait la phase streaming

Le batch entraîne un modèle RF sur 2.6M connexions historiques. Le streaming le charge et l'applique sur du trafic réseau en continu, en temps réel, sans ré-entraînement. Le résultat est visible dans un dashboard SOC live avec des alertes IA.

---

## Architecture

```
Trafic WiFi réel (nfstream)          Dataset CIC-IDS-2017 (parquet)
        ↓                                        ↓
[live_capture.py]                   [dataset_replay.py]
 epoch_000001.csv                    epoch_090001.csv
 epoch_000002.csv                    epoch_090002.csv
        ↓                                        ↓
        └──────────────┬─────────────────────────┘
                       ↓
             data/streaming/predictions/
                       ↓
                  [app.py — Dash]
             dashboard live (port 8060)
                       ↓
         [Claude Haiku via LangChain]
              analyse IA à la demande
```

Deux producteurs écrivent des CSV d'époques dans le même dossier. Le dashboard les consomme toutes les secondes. Ils ne se marchent pas dessus grâce à des plages d'indices séparées.

---

## 1. live_capture.py — la capture WiFi réelle

### Ce que ça fait

Capture le trafic réseau de la carte WiFi via **nfstream**, extrait les features statistiques de chaque flux (durée, volume, timing des paquets…), les passe dans un sklearn RandomForest chargé depuis `data/07_model_output/sklearn_rf_live.pkl`, et écrit les prédictions dans un CSV.

### Pourquoi sklearn ici et pas Spark

nfstream génère les flux en temps réel, un à la fois. Démarrer une SparkSession pour classifier 1 flux ça n'a aucun sens — la latence du driver JVM est plusieurs secondes. Le modèle sklearn (exporté depuis le pipeline Kedro via `sklearn_rf_live.pkl`) répond en millisecondes.

### Ce qu'il produit

```
data/streaming/predictions/epoch_000069.csv   ← 1 ligne = 1 flux réseau classifié
```

Colonnes : `src_ip`, `dst_ip`, `src_port`, `dst_port`, `app_name`, `n_pkts`, `n_bytes`, `predicted_label`, `is_attack`, `detected_at`

**Plage d'époques : 0 → 89 999**

---

## 2. dataset_replay.py — le replay des attaques

### Ce que ça fait

Charge le dataset CIC-IDS-2017 depuis `data/02_intermediate/raw_traffic/data.parquet`, le rejoue batch par batch via le modèle Spark MLlib (le même qui a été entraîné en batch), et écrit les prédictions toutes les 2 minutes environ.

### Pourquoi

En conditions réelles, une carte WiFi de bureau ne génère que du trafic bénin. Pour démontrer la détection d'attaques, il faut rejouer un dataset qui en contient. Le modèle ne connaît pas les labels à l'avance — il reçoit juste les features brutes et prédit.

**Plage d'époques : 90 000+**

---

## 3. La séparation des plages d'époques — pourquoi c'est important

Au début, les deux producteurs partageaient un seul compteur `last_epoch`. Quand `dataset_replay` écrivait `epoch_090000`, le dashboard avançait son curseur à 90000 et **excluait définitivement** tous les fichiers live (0–89999) qui arriveraient après.

**Le fix :** deux curseurs indépendants dans le `data-store` Dash :

```python
{
  "last_live":   -1,       # suit les epochs 0–89999
  "last_replay": 89999,    # suit les epochs 90000+
}
```

Chaque producteur est suivi séparément. Les deux flux restent visibles en permanence.

---

## 4. app.py — le dashboard live (port 8060)

### Architecture

Dash avec 2 routes :
- `/` → landing page (lien vers les deux dashboards + métriques modèle)
- `/live` → dashboard streaming temps réel

### Ce qui se passe chaque seconde (fast-tick)

1. Lecture des nouveaux CSVs d'époques (live + replay, max 30 fichiers)
2. Mise à jour des KPIs (flux total, attaques, taux, niveau de menace)
3. Accumulation des flux dans `recent_rows` (buffer de 500 entrées)
4. Détection d'un nouveau type d'attaque → propose la modale Claude

### Ce qui se passe toutes les 3 secondes (slow-tick)

1. Timeline flux/seconde avec annotations des évènements d'attaque
2. Histogramme des classes détectées (cliquer dessus filtre le feed)
3. Graphe réseau bipartite (attaquants → cibles)
4. Barre de stats de session (durée, flux total, pic, IP top)

### Fonctionnalités démo

| Bouton | Ce que ça fait |
|---|---|
| **⚡ Simuler attaque** | Injecte un batch d'attaques depuis le parquet CIC-IDS-2017, classe aléatoire, réinitialise le cooldown → modale Claude dans la seconde |
| **📥 Rapport** | Génère un rapport HTML complet (5 sections : KPIs, types d'attaques + recommandations, top IPs/protocoles/ports, métriques modèle, derniers flux) |
| **Analyser avec Claude** | Envoie le type d'attaque + métriques du modèle à Claude Haiku → analyse contextuelle dans le panneau IA |
| **Clic sur barre de classes** | Filtre le feed sur cette classe (drill-down) |

---

## 5. Modale + analyse Claude

Quand un nouveau type d'attaque est détecté (et que le cooldown de 5 minutes est écoulé), une modale apparaît :

```
⚠ ALERTE SÉCURITÉ
DoS_Hulk
Attaque en cours

[ Analyser avec Claude ]   [ Ignorer ]
```

Si l'analyste clique "Analyser", Dash appelle `analyze_threat()` depuis `src/netsentinel/agent/threat_analyzer.py` :

```python
analyze_threat(
    attack_type="DoS_Hulk",
    f1=97.3,
    precision=98.1,
    recall=96.8,
    fn=13,
    anthropic_api_key=...,
    langsmith_api_key=...,
)
```

Claude reçoit le contexte complet (type d'attaque, métriques du modèle, nombre de faux négatifs) et produit une analyse structurée : explication de l'attaque, interprétation des performances du modèle, risque opérationnel, recommandations.

---

## 6. Commandes pour la démo

### Démarrer les services

```bash
# Terminal 1 — pipeline batch (si pas encore fait, ~30 min)
kedro run

# Terminal 2 — MLflow UI
mlflow ui
# → http://localhost:5000

# Terminal 3 — Dashboard batch SOC
python dashboard.py
# → http://localhost:8050

# Terminal 4 — Dashboard live + landing
python app.py
# → http://localhost:8060

# Terminal 5 — Capture WiFi réelle
python streaming/live_capture.py

# Terminal 6 — Replay des attaques depuis CIC-IDS-2017
python streaming/dataset_replay.py
```

### Ordre recommandé pour la démo

1. Ouvrir `http://localhost:8060` → landing page
2. Montrer les métriques du modèle (F1, accuracy, classes)
3. Cliquer "Analyse Live" → dashboard streaming
4. Laisser le trafic bénin monter (live_capture)
5. Attendre ou forcer une attaque via `dataset_replay`
6. Cliquer "⚡ Simuler attaque" pour une démonstration immédiate
7. Accepter la modale → analyse Claude en direct
8. Cliquer sur une barre dans "Classes détectées" → drill-down feed
9. Exporter le rapport HTML
10. Switcher vers `http://localhost:8050` → dashboard batch pour les métriques détaillées

---

## Synthèse

| Composant | Rôle | Port |
|---|---|---|
| `live_capture.py` | Capture WiFi réelle → sklearn RF → CSVs | — |
| `dataset_replay.py` | Replay CIC-IDS-2017 → Spark MLlib → CSVs | — |
| `app.py` | Dashboard live + landing page | 8060 |
| `dashboard.py` | Dashboard batch SOC | 8050 |
| `mlflow ui` | Tracking des runs, métriques, artefacts | 5000 |

---

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*
