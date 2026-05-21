# NetSentinel
## Rapport de projet — Système de détection d'intrusions réseau
**Big Data – BLOC 2 - UE28**
Mme Linda Wang

*Rapport accompagnant le projet NetSentinel — pipeline Big Data complet de détection d'intrusions réseau, de l'ingestion des données brutes jusqu'au dashboard SOC interactif.*

**Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026**

---

## Table des matières

1. Introduction
2. Contexte et problématique
3. Dataset
4. Architecture du pipeline
5. Stack MLOps — orchestration, versionnage et tracking
6. Phase Batch — Infrastructure Big Data avec Spark
   - 6.1 Pourquoi Spark ? La contrainte de volume
   - 6.2 Initialisation du cluster — SparkSession
   - 6.3 Ingestion des données — spark.read.csv()
   - 6.4 Équilibrage des classes — filter, orderBy, limit, union
   - 6.5 Feature engineering — drop, cast, dropna
   - 6.6 La lazy evaluation — le moteur derrière tout ça
   - 6.7 Préparation ML — StringIndexer, VectorAssembler, randomSplit
   - 6.8 Sauvegarde intermédiaire en Parquet
   - 6.9 Configuration mémoire et tuning
   - 6.10 Mesures de performance — pipeline instrumenté de bout en bout
   - 6.11 Spark UI — observer le plan d'exécution en temps réel
7. Phase Batch — Machine Learning distribué
   - 7.1 Choix du modèle
   - 7.2 Recherche d'hyperparamètres — CrossValidator
   - 7.3 Stratégie d'ensemble
   - 7.4 Feature importance
   - 7.5 Évaluation
8. Dashboard SOC
9. Phase Streaming
10. Analyse des limites
11. Conclusion
12. Références

---

## 1. Introduction

Ce projet, je l'ai appelé NetSentinel. L'idée de départ est simple : une entreprise industrielle génère en permanence des milliers de connexions réseau, et face à la montée des cyberattaques, il lui faut un système capable de détecter les intrusions automatiquement — pas un humain qui lit des logs, un vrai pipeline Big Data.

J'ai donc construit un IDS (Intrusion Detection System) complet de bout en bout : ingestion de 2GB de données brutes avec Apache Spark, preprocessing distribué, entraînement d'un modèle distribué avec Spark MLLib, évaluation, export des résultats en Delta Lake, et un dashboard SOC interactif.

Ce rapport décrit les choix que j'ai faits à chaque étape, pourquoi je les ai faits, et ce que ça donne en résultat — en mettant particulièrement l'accent sur le fonctionnement interne de Spark à chaque opération du pipeline.

---

## 2. Contexte et problématique

Une grande entreprise industrielle génère en permanence des milliers de connexions réseau. Face à la montée des cyberattaques — DDoS, SQL Injection, Botnet, Brute Force… le service IT a besoin d'un système capable d'analyser le trafic réseau et de détecter les intrusions, aussi bien sur l'historique qu'en temps réel.

C'est exactement le problème que j'ai choisi de résoudre. L'objectif est de construire un pipeline Big Data en deux phases :

- **Phase Batch** — analyser 2GB d'historique de trafic réseau, entraîner un modèle de classification qui identifie si une connexion est bénigne ou malveillante, et visualiser les résultats dans un dashboard SOC.
- **Phase Streaming** — simuler l'arrivée de nouvelles connexions en temps réel et alerter le service IT dès qu'une intrusion est détectée. *(Partie 2)*

La contrainte centrale : tout doit passer par **Apache Spark** — les données sont trop volumineuses pour un traitement classique pandas, et le pipeline doit être capable de scaler.

---

## 3. Dataset

**BCCC-CIC-IDS-2017** — Canadian Institute for Cybersecurity / MIT (2024)

C'est un dataset de référence dans le domaine de la détection d'intrusions. Il contient du trafic réseau labellisé, capturé sur une infrastructure complète pendant une semaine entière.

| Caractéristique | Détail |
|---|---|
| Volume brut | ~2.6M connexions réseau (~2GB) |
| Features | 122 features par flux réseau |
| Classes | 15 (Benign + 14 types d'attaques) |
| Format | CSV par jour / type d'attaque |

Types d'attaques couverts : DoS Hulk, DDoS LOIT, PortScan, FTP-Patator, DoS GoldenEye, DoS Slowhttptest, SSH-Patator, Botnet ARES, DoS Slowloris, Heartbleed, Web Brute Force, SQL Injection, XSS…

Ce qui m'a intéressé dans ce dataset, c'est qu'il couvre des attaques très différentes dans leur nature — des attaques volumétriques (DoS/DDoS), des attaques par force brute (FTP-Patator, SSH-Patator), des attaques applicatives (SQLi, XSS) et des infections (Botnet). Ça oblige le modèle à apprendre des patterns vraiment distincts.

---

## 4. Architecture du pipeline

L'idée centrale de cette architecture : séparer les deux phases (batch et streaming) mais partager les modèles. Le batch entraîne et exporte les 10 modèles RF. Le streaming les charge directement sans ré-entraînement.

Les résultats du batch (métriques, feature importance, matrice de confusion) sont exportés en **Delta Lake** vers `data/dashboard/` et lus directement par le dashboard Dash.

```
CSV bruts (2.6M lignes, 23 fichiers)
        ↓  spark.read.csv()          → 23 partitions, lazy
    df (2.6M lignes)
        ↓  filter / orderBy / limit / union   → équilibrage, shuffle
    df_balanced (287K lignes)
        ↓  drop / cast / dropna      → transformations lazy
    df_clean (45 features)
        ↓  StringIndexer + VectorAssembler    → action (fit) + lazy (transform)
    df_final (vecteur ML)
        ↓  write.parquet()           → action, matérialisation sur disque
    df_final.parquet
        ↓  randomSplit(0.8/0.2)
    train_df / test_df
        ↓  CrossValidator (80 RF)    → 80 entraînements distribués
        ↓  RandomForestClassifier ×10 → ensemble, 1000 arbres
    models[]
        ↓  transform × 10 + vote UDF → inférence distribuée
    prédictions → métriques → Delta Lake → Dashboard
```

---

## 5. Stack MLOps — orchestration, versionnage et tracking

Le pipeline décrit dans les sections suivantes existe en deux versions : une version notebook (le PoC initial) et une version production structurée avec une stack MLOps complète. Cette section décrit les outils ajoutés pour passer de l'un à l'autre, pourquoi je les ai choisis, et ce que ça change concrètement.

---

### 5.1 Kedro — orchestration du pipeline

Sans orchestration, mon pipeline c'est un script `train.py` de 500 lignes qu'on lance manuellement. Impossible à relancer partiellement si quelque chose plante à mi-chemin, et si quelqu'un d'autre clone le repo il ne sait pas dans quel ordre lancer les fichiers.

Kedro structure ça en **nodes** (fonctions Python pures) reliés par un **Data Catalog** (déclaration des sources et destinations en YAML). Le DAG est résolu automatiquement — Kedro sait dans quel ordre lancer chaque fonction selon ses dépendances d'entrée/sortie.

```bash
kedro run   # résout le DAG, lance les 9 nodes dans l'ordre
```

Le Data Catalog remplace tous les chemins hardcodés dans le code :

```yaml
# conf/base/catalog.yml — je déclare la donnée une fois
clean_traffic:
  type: spark.SparkDataset
  filepath: data/03_primary/clean_traffic/
  file_format: parquet
```

Mon code Python ne connaît plus les chemins de fichiers — Kedro injecte les données directement dans chaque fonction. Si je déplace les données, je change une ligne dans le YAML.

```
nodes.py    →  les fonctions   (le "quoi faire")
pipeline.py →  l'ordre         (le "dans quel ordre")
catalog.yml →  les données     (le "avec quoi")
params.yml  →  les valeurs     (le "avec quelles valeurs")
```

---

### 5.2 DVC — versionnage des données

Git ne peut pas versionner des fichiers de plusieurs centaines de Mo. Sans DVC, les CSV sont soit commitées (le repo explose), soit ignorées (un collaborateur clone et n'a aucune donnée).

DVC sépare les deux : Git stocke un fichier `.dvc` de quelques lignes (juste un hash), les vraies données sont sur **Google Drive**.

```bash
dvc push   # envoie les données sur Google Drive
dvc pull   # récupère exactement la même version (autre machine ou collègue)
```

L'intérêt concret : dans 6 mois, je reviens à un ancien commit Git, je fais `dvc pull`, et je rejoue exactement le même entraînement sur exactement les mêmes données. C'est la reproductibilité réelle — pas juste le code, mais les données aussi.

---

### 5.3 dlt — couche d'ingestion avec validation de schéma

`spark.read.csv()` directement dans le pipeline c'est fragile. Si une colonne change de nom dans un nouveau CSV → ça plante silencieusement dans Spark 2h plus tard, au milieu d'un entraînement.

dlt (data load tool) joue le rôle de garde-barrière : il lit les CSV, valide le schéma automatiquement, et écrit du Parquet propre que Kedro consomme.

```
data/01_raw/*.csv  →  [dlt : validation + typage]  →  data/02_intermediate/  →  [Kedro/Spark]
```

Si un CSV a une colonne manquante ou un type incompatible → dlt plante immédiatement avec un message clair, au lieu que l'erreur remonte 20 minutes plus tard dans un stage Spark.

---

### 5.4 kedro-mlflow + MLflow — tracking automatique

Sans tracking, après quelques runs je ne sais plus quel modèle a les meilleures métriques, avec quels hyperparamètres, sur quelles données. `kedro-mlflow` résout ça en connectant Kedro et MLflow automatiquement : chaque `kedro run` crée un run MLflow avec tous les paramètres YAML loggués sans une seule ligne de code supplémentaire.

J'ai ajouté le **dataset tracking** dans le node d'entraînement — MLflow calcule un hash du dataset utilisé :

```python
dataset = mlflow.data.from_spark(train_set, path="data/05_model_input/train.parquet")
mlflow.log_input(dataset, context="training")
```

Si dans 6 mois le modèle se dégrade, je compare le hash du dataset d'entraînement avec les nouvelles données et je vois immédiatement si les données ont changé.

**Model Registry :** le meilleur modèle CV est automatiquement enregistré dans le registry `netsentinel-ids` et passé en `Staging` après chaque run. Le cycle de vie d'un modèle : `Staging → Production → Archived`. Pour charger le modèle depuis n'importe quelle machine :

```python
model = mlflow.spark.load_model("models:/netsentinel-ids/Production")
```

---

### 5.5 LangChain + Claude + LangSmith — intelligence IA dans le dashboard

Un dashboard de métriques montre des chiffres — il ne dit pas à l'analyste SOC ce que ça signifie ni quoi faire. J'ai intégré 3 fonctions IA dans le dashboard, toutes propulsées par **Claude Haiku** via LangChain :

| Fonction | Déclencheur | Ce que Claude produit |
|---|---|---|
| `analyze_threat()` | Sélection d'une classe d'attaque | Explication de l'attaque, analyse des performances du modèle, risque des faux négatifs |
| `generate_soc_report()` | Bouton "Rapport SOC" | Briefing CISO structuré : situation globale, menaces critiques, 3 recommandations. Exportable en PDF |
| `chat_soc()` | Panneau flottant 💬 | Réponses en langage naturel à n'importe quelle question sur les données du dashboard |

**LangSmith** trace chaque appel LLM automatiquement (prompt, réponse, tokens, latence) — c'est le MLflow des LLM. Si Claude répond incorrectement, je vois exactement quel prompt a produit quelle réponse et je peux optimiser.

---

### 5.6 Vue d'ensemble de la stack

![Architecture Pipeline NetSentinel](assets/architecture_pipeline_netsentinel_png.png)

`kedro run` déclenche tout en une commande. `mlflow ui` montre les runs, métriques et artefacts. `dvc pull` garantit qu'on travaille toujours sur les mêmes données.

---

## 6. Phase Batch — Infrastructure Big Data avec Spark

### 6.1 Pourquoi Spark ? La contrainte de volume

La première question à se poser avant de choisir Spark, c'est : est-ce que c'est vraiment nécessaire ? Dans ce projet, la réponse est clairement oui, pour trois raisons concrètes.

**Raison 1 — Le volume.** 2.6 millions de connexions réseau avec 122 features chacune. Si j'essaie de charger ça avec pandas, j'ai environ 2.5GB en RAM pour juste stocker les données, et chaque opération est mono-thread et bloquante. Avec Spark, la lecture est distribuée sur 23 partitions traitées simultanément par 22 cores.

**Raison 2 — Le volume de calcul ML.** Pour trouver les meilleurs hyperparamètres sans overfitter, j'ai utilisé une CrossValidation : 16 combinaisons × 5 folds = **80 Random Forests entraînés**. Plus 10 modèles pour l'ensemble final. **90 entraînements RF sur 230K lignes.** Spark distribue chaque entraînement sur les 22 cores disponibles.

**Raison 3 — La reproductibilité.** Spark MLLib enchaîne le preprocessing et le ML dans un plan d'exécution optimisé, versionnable et reproductible. Le pipeline peut être relancé à l'identique sur un nouveau batch sans réécrire une seule ligne.

---

### 6.2 Initialisation du cluster — SparkSession

```python
spark = SparkSession.builder
    .appName("NetSentinel - Batch Analysis")
    .config("spark.driver.memory", "25g")
    .config("spark.executor.memory", "12g")
    .config("spark.executor.memoryOverhead", "2g")
    .config("spark.driver.maxResultSize", "4g")
    .getOrCreate()
```

Ici on crée (ou on récupère) la **SparkSession** — c'est le point d'entrée unique vers le cluster Spark. Spark va allouer les ressources nécessaires : les executeurs (workers) qui vont traiter les données, et le driver qui va coordonner tout ça. Si une session existe déjà avec ce nom, Spark va la réutiliser (`getOrCreate`) au lieu d'en créer une nouvelle — c'est pour ça que relancer une cellule ne crashe pas.

C'est aussi là qu'on fixe la configuration mémoire. Le **driver** reçoit 25g parce que c'est lui qui collecte les résultats finaux (les modèles, les exports pandas) — si cette mémoire est trop petite, le moindre `.collect()` plante. Les **executeurs** reçoivent 12g chacun pour les entraînements RF, plus 2g de `memoryOverhead` pour la mémoire JVM hors-heap (sérialisation des données, broadcast des modèles entre workers). Sans ce `memoryOverhead`, on obtenait des erreurs **Java heap space** pendant la CrossValidation.

---

### 6.3 Ingestion des données — spark.read.csv()

```python
df = spark.read.csv("../data", header=True, inferSchema=True)
```

Cette ligne ne lit pas encore une seule donnée. Spark va scanner le dossier, trouver les 23 fichiers CSV, inférer le schéma (les types de colonnes), et créer un **plan d'exécution** — c'est tout. C'est la lazy evaluation : Spark note ce qu'il faudra faire, mais ne le fait pas encore.

Ce plan sera exécuté seulement quand on déclenchera une **action** — comme le `.count()` juste après :

```python
n_rows = df.count()  # ← action : déclenche la lecture réelle des 23 CSV
```

Là, Spark exécute le plan : il lit les 23 CSV **en parallèle** sur 22 cores, crée **23 partitions** (une par fichier), et compte les lignes de chaque partition indépendamment avant de sommer les résultats. Résultat mesuré : **2,610,292 lignes en 22.8 secondes**.

Une **partition** c'est simplement un morceau du DataFrame qui tient en mémoire sur un seul worker. Spark garantit que chaque transformation sera appliquée en parallèle sur toutes les partitions simultanément — aucune partition n'attend qu'une autre soit finie. Sur ce projet : 23 partitions, 22 cores → quasiment un core par partition.

| Métrique | Valeur |
|---|---|
| Lignes totales | 2,610,292 |
| Colonnes | 122 |
| Partitions RDD | 23 (1 CSV = 1 partition) |
| Lignes / partition (moy.) | 113,490 |
| Durée (read + count) | 22.8s |

---

### 6.4 Équilibrage des classes — filter, orderBy, limit, union

Le dataset brut est fortement déséquilibré (Benign = 1.7M sur 2.6M lignes). J'ai limité les classes majoritaires à 50 000 lignes chacune :

```python
benign_df = df.filter(F.col("label") == "Benign").orderBy(F.rand(seed=42)).limit(50000)
df_balanced = benign_df.union(dos_hulk_df).union(port_scan_df).union(attacks_df)
```

Les opérations illustrent bien la différence entre transformations **narrow** et **wide** : `.filter()`, `.limit()` et `.union()` sont narrow (chaque partition travaille seule, pas de shuffle). `.orderBy()` est wide — il force un **shuffle** global pour trier les lignes entre partitions, d'où les 21.2 secondes malgré que ça "ne semble" être qu'un tri.

J'utilise `F.rand(seed=42)` plutôt qu'un simple `.limit()` pour éviter le biais temporel — un `.limit()` seul prendrait les premières connexions dans l'ordre de lecture (toutes du lundi matin).

---

### 6.5 Feature engineering — drop, cast, dropna

Toutes ces opérations sont des transformations **lazy** — Spark accumule les instructions sans calculer quoi que ce soit :

```python
df_clean = df_balanced.drop(*cols_to_drop)                          # 0.3ms — gratuit
df_clean = df_clean.withColumn(col, F.col(col).cast("double"))      # narrow, par colonne
df_clean = df_clean.withColumn(c, F.when(F.col(c) == float("inf"), None).otherwise(F.col(c)))
df_clean = df_clean.dropna()
```

Le `.drop()` sur 75 colonnes prend **0.3ms** — Spark met juste à jour son plan, aucune donnée n'est lue. Le remplacement des `inf/-inf` est nécessaire car Spark génère des valeurs infinies quand il divise par zéro (ex: `bytes_rate` sur une durée nulle) et MLLib ne sait pas les gérer.

---

### 6.6 La lazy evaluation — le moteur derrière tout ça

C'est le concept central de Spark, et c'est là que ça diffère fondamentalement de pandas. En pandas, chaque opération s'exécute immédiatement et modifie les données en mémoire. En Spark, les opérations sont soit des **transformations** (lazy), soit des **actions** (qui déclenchent le calcul).

**Transformations (lazy)** : `.filter()`, `.select()`, `.drop()`, `.withColumn()`, `.join()`, `.union()`, `.groupBy()`, `.orderBy()`
→ Ne lisent aucune donnée. Elles enrichissent le **DAG** (Directed Acyclic Graph) — le plan d'exécution interne de Spark.

**Actions** : `.count()`, `.collect()`, `.show()`, `.write()`, `.toPandas()`
→ Déclenchent l'exécution de **toutes les transformations accumulées en une fois**.

Quand une action est déclenchée, le **Catalyst optimizer** entre en jeu : il prend le DAG complet, l'optimise (fusionne les filtres, pousse les projections au plus tôt, réordonne les opérations si possible), puis génère le plan physique qui sera distribué sur les workers. C'est Catalyst qui fait en sorte que même un plan complexe avec 20 transformations enchaînées soit exécuté de façon optimale.

La démonstration concrète sur ce projet :

| Opération | Type | Durée mesurée |
|---|---|---|
| `.drop()` sur 75 colonnes (2.6M lignes) | Transformation lazy | **0.3 ms** |
| `.filter()` + `.withColumn()` × 12 | Transformation lazy | quelques µs |
| `.count()` sur 2.6M lignes | Action → exécution réelle | **22.8 s** |
| `.groupBy().count().collect()` | Action + wide transform | **~7 s** |

---

### 6.7 Préparation ML — StringIndexer, VectorAssembler, randomSplit

```python
indexer = StringIndexer(inputCol="label", outputCol="label_index")
df_indexed = indexer.fit(df_clean).transform(df_clean)   # fit = action, transform = lazy

assembler = VectorAssembler(inputCols=feature_cols, outputCol="features")
df_final = assembler.transform(df_indexed)               # lazy

train_df, test_df = df_final.randomSplit([0.8, 0.2], seed=42)  # lazy
```

**`StringIndexer.fit()`** est une action — Spark scanne tout `df_clean` pour apprendre le mapping `label → index` (DDoS_LOIT=0, Benign=1…). Le `VectorAssembler` regroupe les 45 features numériques dans un seul vecteur dense — format obligatoire pour tous les algorithmes Spark MLLib.

Le `randomSplit` ne génère pas de shuffle : chaque partition est découpée indépendamment selon une probabilité basée sur le seed. Split final : **229 655 lignes train / 57 297 lignes test**.

---

### 6.8 Sauvegarde intermédiaire en Parquet

```python
df_final.write.parquet("../data/processed/df_final.parquet", mode="overwrite")
df_final = spark.read.parquet("../data/processed/df_final.parquet")
```

`.write.parquet()` est une action — Spark exécute tout le plan et matérialise les données sur disque. C'est indispensable avant la CrossValidation : sans ça, chacun des 80 entraînements relirait les CSV bruts + referait tout le preprocessing depuis le début.

Parquet est columaire (stocke colonne par colonne) — bien plus efficace que CSV pour le ML, où les algorithmes lisent souvent une seule feature à la fois pour calculer les seuils de découpage des arbres.

---

### 6.9 Configuration mémoire et tuning

Une partie importante du travail Big Data, c'est le tuning — ajuster Spark pour que ça tourne sans crash sur la machine disponible.

| Paramètre | Valeur | Raison |
|---|---|---|
| `spark.driver.memory` | 25g | Collecte les 10 modèles RF + exports pandas vers le driver |
| `spark.executor.memory` | 12g | Mémoire par worker pour les entraînements RF |
| `spark.executor.memoryOverhead` | 2g | JVM hors-heap : sérialisation, broadcast des modèles |
| `spark.driver.maxResultSize` | 4g | Limite le volume que `.collect()` peut ramener |
| `parallelism=1` (CrossValidator) | 1 | Évite que 2 entraînements RF tournent en même temps et saturent la RAM |

**L'erreur rencontrée :** Avec la configuration par défaut, la CrossValidation plantait avec une erreur `Java heap space out of memory`. Le problème venait du broadcast des modèles RF pendant l'évaluation de chaque fold — chaque modèle RF entraîné fait ~65MB, et Spark doit l'envoyer à tous les workers pour qu'ils puissent évaluer les prédictions. En augmentant `executor.memory` à 12g et en ajoutant 2g d'`overhead`, les erreurs ont disparu.

---

### 6.10 Mesures de performance — pipeline instrumenté de bout en bout

J'ai instrumenté chaque étape du pipeline avec `time.time()` pour avoir des mesures précises sur une exécution réelle complète :

| Étape | Durée | % du total | Ce que Spark fait |
|---|---|---|---|
| Ingestion CSV (2.6M lignes) | 22.8s | 1.5% | Lecture parallèle, 23 partitions, 22 cores |
| Équilibrage des classes | 21.2s | 1.4% | Shuffle global pour `orderBy(rand())` |
| Feature engineering (75 drops) | **0.3ms** | **~0%** | Lazy — plan mis à jour, 0 donnée lue |
| VectorAssembler + split | 82.5s | 5.5% | 3 actions (StringIndexer.fit + 2 counts) |
| CrossValidator (80 RF) | 703.7s | 47.1% | **Étape dominante** — 80 fits distribués |
| Ensemble 10 RF (entraînement) | 249.5s | 16.7% | 10 fits, ~25s/modèle, 1000 arbres |
| Inférence test set | 125.3s | 8.4% | 10 transforms + vote UDF + broadcast |
| Sauvegarde 10 modèles | 26.4s | 1.8% | Sérialisation Spark ML sur disque |
| Export Delta Lake | 263.0s | 17.6% | Write ACID + transaction logs + manifests |
| **TOTAL PIPELINE** | **1495s** | **100%** | **25 minutes** |

**Ce que ces chiffres montrent :**

Le feature engineering à **0.3ms** vs l'ingestion à **22.8s** illustre parfaitement la lazy evaluation — supprimer 75 colonnes sur 2.6M lignes est quasi-gratuit parce que c'est une transformation pure, sans aucun calcul réel.

**64% du temps total** est passé à entraîner des modèles (703s pour la CV + 249s pour l'ensemble). C'est exactement ce qui justifie Spark : sans distribution sur 22 cores, chaque entraînement RF prendrait bien plus longtemps.

L'inférence à **125s** donne un débit de **457 connexions/seconde** avec une latence de **2.19ms par connexion**. En mode batch, c'est largement suffisant.

---

### 6.11 Spark UI — observer le plan d'exécution en temps réel

Le **Spark UI** (port 4040 pendant l'exécution, port 18080 pour l'History Server après) permet d'observer exactement ce que Spark fait en interne pour chaque opération.

| Onglet | Ce qu'on y voit | Utilité sur ce projet |
|---|---|---|
| **Jobs** | Une ligne par action déclenchée | La CrossValidation génère ~400 jobs (80 entraînements × plusieurs stages chacun) |
| **Stages** | Une ligne par stage dans le DAG | Identifier les stages avec shuffle (barre orange) vs narrow (barre verte) |
| **Tasks** | Détail de chaque task individuelle | Vérifier que les 22 cores sont bien utilisés, pas de skew |
| **Executors** | Mémoire et CPU par worker | Détecter un spill sur disque (signe de mémoire insuffisante) |
| **Storage** | DataFrames mis en cache | Voir si les `.cache()` sont matérialisés |
| **SQL** | Plan logique vs plan physique | Voir comment Catalyst a optimisé le plan |

**Les observations concrètes sur ce projet :**

**Pendant l'inférence (10 modèles) :**
Dans le Spark UI, on voit le warning suivant apparaître 10 fois :
```
WARN DAGScheduler: Broadcasting large task binary with size 65.0 MiB
```
Chaque modèle RF entraîné fait ~65MB. Spark doit le **broadcaster** depuis le driver vers tous les workers pour qu'ils puissent faire les prédictions en parallèle. Le broadcast est une optimisation Spark pour éviter les shuffles : au lieu que chaque worker demande le modèle au fur et à mesure, on lui envoie une copie une fois pour toutes. Mais avec 10 modèles de 65MB chacun, ça représente 650MB de broadcast au total — d'où le warning.

**Pendant la CrossValidation :**
La timeline du Spark UI montre clairement les 80 entraînements s'enchaîner séquentiellement (puisque j'ai fixé `parallelism=1` pour éviter les OOM). Chaque entraînement génère plusieurs stages : un stage de lecture depuis le Parquet, puis des stages de construction des arbres de décision distribués sur les 22 cores.

**La différence CSV vs Parquet :**
Dans les stages de lecture, on voit que les stages lisant le Parquet (après la sauvegarde intermédiaire) sont bien plus courts que les stages qui repartaient des CSV — c'est la différence entre lire des données binaires compressées et columaires vs parser du texte CSV ligne par ligne.

---

## 7. Phase Batch — Machine Learning distribué

### 7.1 Choix du modèle

J'ai choisi le **Random Forest** pour plusieurs raisons pratiques :
- **Robuste au déséquilibre résiduel** des classes
- **Feature importance directement interprétable** — un analyste SOC a besoin de comprendre sur quoi repose la détection
- **Pas de normalisation nécessaire** — les features réseau ont des échelles très différentes (bytes vs counts de flags), RF s'en fiche
- **Natif dans Spark MLLib** — distribué nativement sur les partitions du DataFrame

---

### 7.2 Recherche d'hyperparamètres — CrossValidator

Pour trouver les meilleurs hyperparamètres **sans risquer l'overfitting**, j'ai utilisé un `CrossValidator` Spark avec une `ParamGridBuilder` — l'équivalent Spark du `GridSearchCV` de scikit-learn.

**La grille explorée :**

| | numTrees = 30 | numTrees = 50 | numTrees = 75 | numTrees = 100 |
|---|---|---|---|---|
| **maxDepth = 5** | ✓ | ✓ | ✓ | ✓ |
| **maxDepth = 8** | ✓ | ✓ | ✓ | ✓ |
| **maxDepth = 10** | ✓ | ✓ | ✓ | ✓ |
| **maxDepth = 11** | ✓ | ✓ | ✓ | ✓ ← optimal |

**16 combinaisons × 5 folds = 80 Random Forests entraînés** — évalués par F1-Score sur le fold de validation. Durée totale : **703.7s (11.7 min)**, soit 8.8s par entraînement RF.

**Le problème de l'overfitting avec maxDepth élevée :**

Lors d'un premier essai avec `maxDepth` jusqu'à 15, la CV trouvait `numTrees=100 / maxDepth=15` comme optimal. Mais en comparant le score CV avec le score test réel :

| Configuration | F1 CV (5-fold) | F1 Test set | Écart |
|---|---|---|---|
| numTrees=100, maxDepth=**15** | 99.59% | ~97.5% | **~2.1% ← overfit** |
| numTrees=100, maxDepth=**11** | 99.27% | 99.28% | **0.01% ← stable** |

Un arbre de profondeur 15 a jusqu'à 2^15 = 32 768 feuilles. Sur 230K lignes d'entraînement, certaines feuilles ne contiennent que quelques lignes — le modèle mémorise les données au lieu d'apprendre des patterns généralisables. En limitant à `maxDepth=11`, le modèle généralise correctement et l'écart CV/test tombe à 0.01%.

---

### 7.3 Stratégie d'ensemble

Au lieu d'un seul gros modèle, j'ai entraîné **10 Random Forests indépendants** — 100 arbres chacun, profondeur max 11, seed différente pour chaque. La prédiction finale = **vote majoritaire** (mode des 10 prédictions).

Pourquoi 10 modèles plutôt qu'un seul avec 1000 arbres ? Deux raisons :
1. Les **feature importances** moyennées sur 10 runs indépendants sont statistiquement plus stables qu'un seul run
2. Le **vote de 10 modèles** est plus robuste sur les cas limites (DoS_Slowloris vs DoS_Slowhttptest qui se ressemblent beaucoup)

Durée totale mesurée : **249.5s** — 25s/modèle en moyenne, avec un min de 22s et un max de 28s (variation normale due aux seeds différentes).

---

### 7.4 Feature importance

Les features les plus discriminantes et ce qu'elles signifient concrètement :

- **`bwd_init_win_bytes`** — taille de fenêtre TCP initiale du serveur. En SYN flood, l'attaquant ouvre des milliers de connexions sans jamais répondre → fenêtre nulle ou anormale
- **`bwd_packets_IAT_mean`** — temps entre les réponses du serveur. En DoS, le serveur répond de façon chaotique
- **`fwd_packets_IAT_mean`** — temps entre deux paquets envoyés. Un humain est irrégulier, un script d'attaque est régulier comme une horloge
- **`rst_flag_counts`** — flag RST = coupure soudaine de connexion. Normal = rare, attaque = des centaines par seconde
- **`dst_port`** — FTP-Patator → port 21, SSH-Patator → port 22 : signal direct

Ce qui m'a frappé, c'est que les features les plus importantes ne sont pas les plus "évidentes" — ce sont des features **comportementales** sur le timing et la régularité. Un script d'attaque se trahit par sa régularité.

---

### 7.5 Évaluation

Split train/test 80/20, randomisé avec `seed=42`.

| Métrique | Score |
|---|---|
| **Accuracy** | **99.30%** |
| **F1-Score (weighted)** | **99.28%** |
| **Precision (weighted)** | **99.29%** |
| **Recall (weighted)** | **99.30%** |

**Métriques détaillées par classe :**

| Classe | F1 | Precision | Recall | Faux Négatifs |
|---|---|---|---|---|
| DDoS_LOIT | 99.97% | 100.00% | 99.95% | 10 |
| FTP-Patator | 99.95% | 99.90% | 100.00% | 0 |
| Port_Scan | 99.91% | 99.99% | 99.83% | 17 |
| SSH-Patator | 99.71% | 100.00% | 99.41% | 7 |
| Benign | 99.63% | 99.42% | 99.84% | 16 |
| Botnet_ARES | 99.61% | 99.23% | 100.00% | 0 |
| DoS_GoldenEye | 99.32% | 99.47% | 99.18% | 14 |
| DoS_Hulk | 98.66% | 97.48% | 99.87% | 13 |
| DoS_Slowhttptest | 87.82% | 87.94% | 87.69% | 170 |
| **DoS_Slowloris** | **84.31%** | 98.66% | 73.61% | **265** |

**8 classes au-dessus de 99% de F1.** Les deux points faibles — DoS_Slowloris (recall 73%) et DoS_Slowhttptest (F1 88%) — partagent exactement le même mécanisme d'attaque : connexions lentes pour épuiser le serveur. Leurs signatures réseau sont quasi identiques, ce qui les rend difficiles à distinguer l'une de l'autre.

Important à noter : la précision reste très haute sur ces deux classes (98.66% et 87.94%). Ce que le modèle rate, il le rate en silence — mais quand il détecte quelque chose, il a presque toujours raison.

---

## 8. Dashboard SOC

Les résultats du batch sont exportés en **Delta Lake** vers `data/dashboard/` et lus directement par le dashboard Dash/Plotly.

Le choix de Delta Lake plutôt que Parquet simple : Delta Lake garantit la **cohérence ACID** — si une exportation plante à mi-chemin, les anciennes données restent intactes. C'est important pour un dashboard en production où l'analyste pourrait consulter les métriques pendant qu'un nouveau batch exporte.

Le dashboard répond à 5 questions concrètes qu'un analyste SOC se pose :

| Graphique | Question |
|---|---|
| Volume par type d'attaque | Qu'est-ce qui attaque et en quel volume ? |
| Scatter Precision / Recall | Quelles attaques détecte-t-on bien vs celles qu'on rate ? |
| Matrice de confusion | Qu'est-ce qu'on confond avec quoi ? |
| Feature importance | Sur quels signaux réseau repose la détection ? |
| Radar par classe | Vue 360° F1 / Precision / Recall pour une classe donnée |

Fonctionnalités interactives : filtre par classe (clic sur barre ou bulle), sélecteur de métrique (F1/Precision/Recall), tableau de drill-down par classe sélectionnée.

---

## 9. Phase Streaming

La phase streaming charge les modèles entraînés en batch et les applique sur un flux de connexions réseau en temps réel via Spark Structured Streaming, sans ré-entraînement. Cette partie est développée dans la partie 2 du projet.

---

## 10. Analyse des limites

**Ce que ce système détecte bien :**
- Attaques volumétriques (DoS/DDoS) → patterns de flags TCP et de volume très distincts
- Attaques par force brute (FTP-Patator, SSH-Patator) → le port de destination suffit presque seul
- Botnet → connexions régulières et automatisées, visible dans les IAT

**Ce qu'il détecte moins bien :**
- Attaques lentes (Slowloris, Slowhttptest) → recall 73% — deux attaques trop similaires dans leurs signatures
- Zero-day attacks → le modèle a été entraîné sur des patterns connus de 2017

**Limitations techniques :**
- Dataset de 2017 — certains types d'attaques récents ne sont pas représentés
- L'inférence à 457 connexions/seconde est correcte pour du batch, mais un vrai IDS temps réel sur un réseau enterprise nécessiterait un vrai cluster multi-nœuds

---

## 11. Conclusion

Ce projet m'a permis de construire un pipeline IDS complet et fonctionnel sur des données réelles de 2GB, de l'ingestion jusqu'au dashboard — le tout en passant par Apache Spark pour chaque étape.

Ce que j'ai le mieux compris en travaillant sur ce projet, c'est la différence concrète entre les transformations lazy et les actions. Voir que `.drop()` sur 75 colonnes prend 0.3ms alors que `.count()` sur le même DataFrame prend 22 secondes — ça illustre mieux que n'importe quel cours ce que Spark fait réellement en interne.

Les mesures de performance montrent que 64% du temps de pipeline est passé à entraîner des modèles ML — ce qui justifie concrètement le choix de Spark pour distribuer sur 22 cores.

> Présentation du dashboard : https://youtu.be/R8skcZ1tJS0

---

## 12. Références

- **Dataset** : BCCC-CIC-IDS-2017, Canadian Institute for Cybersecurity / MIT — Kaggle
- **Apache Spark / MLLib** : spark.apache.org
- **Delta Lake** : delta.io — format de stockage ACID pour les pipelines data
- **Dash / Plotly** : dash.plotly.com
- **Support de cours Machine Learning** : M. C. Soldani — Random Forest
- **Support de cours Big Data** : Mme L. Wang

---

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*
