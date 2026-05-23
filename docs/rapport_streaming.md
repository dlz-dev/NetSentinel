# NetSentinel — Rapport technique : phase streaming
**Big Data – BLOC 2 - UE28**

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*

---

## Ce que fait la phase streaming

Le batch entraîne un modèle RF sur 2.6M connexions historiques. Le streaming le charge et l'applique sur du trafic réseau en continu, en temps réel, sans ré-entraînement. Le résultat est visible dans un dashboard SOC live avec des alertes et une analyse IA.

---

## Architecture

```
Trafic WiFi réel (nfstream)        Dataset CIC-IDS-2017 (parquet)
        |                                        |
[live_capture.py]                   [dataset_replay.py]
        |                                        |
        +------------------+---------------------+
                           |
                    Kafka topic
                  (localhost:9092)
                           |
                  [kafka_consumer.py]
               inference Spark MLlib RF
                           |
              data/streaming/predictions/
               epoch_000001.csv ...
                           |
               [app.py - Dash, port 8060]
               dashboard live temps reel
                           |
              alerte modale si attaque
                           |
              [Claude Haiku - LangChain]
               analyse IA a la demande
                           |
               [LangSmith EU - observabilite]
```

Les deux producteurs envoient leurs flux dans le meme topic Kafka. Le consumer unique lit tout, classifie avec Spark, et ecrit les predictions. Plages d'indices separees pour ne pas melanger les deux sources dans le dashboard.

---

## 1. live_capture.py — capture WiFi reelle

### Ce que ca fait

Capture le trafic reseau de la carte WiFi via **nfstream**, extrait les features statistiques de chaque flux (duree, volume, timing des paquets...) et les envoie dans le **topic Kafka** `netsentinel-flows` sous forme de messages JSON.

### Pourquoi Kafka ici

nfstream genere les flux en temps reel, un a la fois. Plutot que d'ecrire directement en CSV (couplage fort, concurrence d'acces), on publie dans Kafka. Le consumer peut alors consommer a son rythme, appliquer le modele sur un micro-batch, et ecrire les resultats proprement.

**Plage d'epoques : 0 -> 89 999**

---

## 2. dataset_replay.py — replay des attaques

### Ce que ca fait

Charge le dataset CIC-IDS-2017 depuis `data/02_intermediate/raw_traffic/data.parquet` et rejoue les flux batch par batch dans le **topic Kafka**, en simulant l'arrivee de trafic contenant des attaques reelles.

### Pourquoi

En conditions reelles, une carte WiFi de bureau ne genere que du trafic benin. Pour demontrer la detection d'attaques, il faut rejouer un dataset qui en contient. Le modele ne connait pas les labels a l'avance — il recoit les features brutes et predit.

**Plage d'epoques : 90 000+**

---

## 3. kafka_consumer.py — inference Spark

### Ce que ca fait

Consomme le topic Kafka `netsentinel-flows`, regroupe les messages en micro-batchs, applique le **modele Random Forest Spark MLlib** charge depuis `mlruns/`, et ecrit les predictions dans `data/streaming/predictions/`.

### Pourquoi Spark ici et pas sklearn

Le consumer traite les flux par batchs (plusieurs dizaines a la fois), ce qui justifie Spark. De plus, c'est exactement le meme modele que celui entraine en batch — pas de divergence entre l'environnement d'entrainement et l'environnement d'inference.

```
1 message Kafka = 1 flux reseau (features brutes)
N messages groupes = 1 micro-batch -> inference Spark -> 1 epoch_XXXXXX.csv
```

---

## 4. La separation des plages d'epoques

Au debut, les deux producteurs partageaient un seul compteur `last_epoch`. Quand `dataset_replay` ecrivait `epoch_090000`, le dashboard avancait son curseur a 90000 et **excluait definitivement** tous les fichiers live (0-89999) qui arriveraient apres.

**Le fix :** deux curseurs independants dans le `data-store` Dash :

```python
{
  "last_live":   -1,     # suit les epochs 0-89999  (live_capture)
  "last_replay": 89999,  # suit les epochs 90000+   (dataset_replay)
}
```

Chaque producteur est suivi separement. Les deux flux restent visibles en permanence.

---

## 5. app.py — dashboard live (port 8060)

### Architecture

Dash avec 2 routes :
- `/` -> landing page (liens vers les deux dashboards + metriques modele)
- `/live` -> dashboard streaming temps reel

### Ce qui se passe chaque seconde (fast-tick)

1. Lecture des nouveaux CSVs d'epoques (live + replay, max 30 fichiers)
2. Mise a jour des KPIs (flux total, attaques, taux, niveau de menace)
3. Accumulation des flux dans `recent_rows` (buffer de 500 entrees)
4. Detection d'un nouveau type d'attaque -> propose la modale Claude

### Ce qui se passe toutes les 3 secondes (slow-tick)

1. Timeline flux/seconde avec annotations des evenements d'attaque
2. Histogramme des classes detectees (cliquer dessus filtre le feed)
3. Graphe reseau bipartite (attaquants -> cibles)
4. Barre de stats de session (duree, flux total, pic, IP top)

### Fonctionnalites demo

| Bouton | Ce que ca fait |
|---|---|
| **Simuler attaque** | Injecte un batch d'attaques depuis le parquet CIC-IDS-2017, classe aleatoire, reinitialise le cooldown -> modale Claude dans la seconde |
| **Rapport** | Genere un rapport HTML complet (KPIs, types d'attaques + recommandations, top IPs/protocoles/ports, metriques modele, derniers flux) |
| **Analyser avec Claude** | Envoie le type d'attaque + metriques du modele a Claude Haiku -> analyse contextuelle dans le panneau IA |
| **Clic sur barre de classes** | Filtre le feed sur cette classe (drill-down) |

---

## 6. Modale + analyse Claude Haiku

Quand un nouveau type d'attaque est detecte (cooldown de 5 minutes ecoule), une modale apparait :

```
ALERTE SECURITE
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

Claude recoit le contexte complet (type d'attaque, metriques du modele, faux negatifs) et produit une analyse structuree en markdown :
- Mecanisme de l'attaque
- Interpretation des performances du modele
- Risque operationnel des faux negatifs
- Recommandations SOC concretes

Les appels LLM sont traces dans **LangSmith** (endpoint EU : `eu.api.smith.langchain.com`) pour l'observabilite — duree, tokens, erreurs.

---

## 7. Kafka — pourquoi ce choix

| Sans Kafka | Avec Kafka |
|---|---|
| live_capture et dataset_replay ecrivent directement en CSV | Les producteurs publient dans un topic, le consumer lit a son rythme |
| Couplage fort : si le dashboard est lent, des fichiers s'accumulent | Decouplage complet : chaque composant est independant |
| Concurrence d'acces sur les fichiers CSV | Kafka gere la file, un seul consumer ecrit les CSV |
| Pas de buffer si un composant redémarre | Kafka rejoue les messages non consommes |

Le topic Kafka est configure en **KRaft** (sans ZooKeeper) dans un seul conteneur Docker. Les messages sont stockes sur le volume Docker `kafka-data` (SSD local).

---

## 8. Lancer la demo

```powershell
# Tout en une commande
.\start_demo.ps1

# Puis injecter du trafic
python streaming/dataset_replay.py
```

Le script `start_demo.ps1` lance dans l'ordre : Docker (Kafka + Spark), dashboard batch, dashboard live, Kafka consumer. Il attend que Kafka soit ready sur le port 9092 avant de continuer.

### Ordre recommande pour la demo

1. Ouvrir `http://localhost:8060` -> landing page, montrer les metriques du modele
2. Cliquer "Analyse Live" -> dashboard streaming
3. Lancer `dataset_replay.py` -> trafic avec attaques
4. Ou cliquer "Simuler attaque" pour une demonstration immediate
5. Accepter la modale -> analyse Claude en direct
6. Cliquer sur une barre dans "Classes detectees" -> drill-down feed
7. Exporter le rapport HTML
8. Switcher vers `http://localhost:8050` -> dashboard batch pour les metriques detaillees

---

## Synthese

| Composant | Role | Port |
|---|---|---|
| `live_capture.py` | Capture WiFi reelle -> Kafka | — |
| `dataset_replay.py` | Replay CIC-IDS-2017 -> Kafka | — |
| `kafka_consumer.py` | Kafka -> inference Spark RF -> CSVs | — |
| `app.py` | Dashboard live + landing page | 8060 |
| `dashboard.py` | Dashboard batch SOC | 8050 |
| Kafka | Bus de messages (KRaft, Docker) | 9092 |
| Kafka UI | Interface web Kafka | 8090 |
| Spark UI | Monitoring Spark | 8080 |

---

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*
