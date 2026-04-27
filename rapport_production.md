# NetSentinel — Rapport technique : mise en production

## Ce que j'ai construit et pourquoi

Ce rapport couvre tout ce que j'ai mis en place pour passer du notebook PoC à un pipeline ML **reproductible, tracé et déployable**. Chaque outil a une raison précise d'être là — j'explique le problème qu'il résout avant d'expliquer comment il fonctionne.

---

## L'architecture globale

```
CSV bruts (data/01_raw/)
    ↓
[DVC]           → versionne les données sur Google Drive
    ↓
[dlt]           → valide le schéma, convertit en Parquet
    ↓
[Kedro]         → orchestre le pipeline en DAG
    ↓
[Spark ML]      → preprocessing + CrossValidator + ensemble
    ↓
[kedro-mlflow]  → track params, datasets, métriques automatiquement
    ↓
[MLflow]        → Model Registry, artefacts binaires, UI de comparaison
    ↓
[Dash]          → dashboard SOC interactif
    ↓
[Claude/LangSmith] → analyse IA des menaces + traçabilité des LLM
```

Chaque couche est indépendante. Si je change le modèle ML, le dashboard ne change pas. Si je change la source de données, le modèle ML ne change pas. C'est ça le principe.

---

## 1. Kedro — le squelette du projet

### Pourquoi

Sans Kedro, mon pipeline c'est un notebook ou un script `train.py` monolithique de 500 lignes. Difficile à tester, impossible à relancer partiellement, et si quelqu'un d'autre clone le repo il ne sait pas dans quel ordre lancer les fichiers.

Kedro structure ça en **nodes** (fonctions pures Python) reliés par un **Data Catalog** (déclaration des données en YAML). Le DAG est résolu automatiquement.

### Comment j'ai initialisé le projet

```bash
pip install kedro
kedro new --starter=spaceflights-pyspark
```

`kedro new` crée la structure complète. Le starter `spaceflights-pyspark` est un template pré-configuré pour Spark — j'ai gardé la structure et la config, puis remplacé le code d'exemple par NetSentinel.

### La structure qui en résulte

```
conf/base/
    catalog.yml                    → "où sont les données"
    parameters.yml                 → "quelles valeurs utiliser"
    parameters_data_science.yml    → grille CV, best_params, n_models

src/netsentinel/pipelines/
    data_processing/               → ingest, balance, select_features
    data_science/                  → prepare_features, train_cv, train_ensemble
    reporting/                     → evaluate, export_dashboard
```

### L'analogie qui m'a aidé à comprendre

```
nodes.py    →  les fonctions   (le "quoi faire")
pipeline.py →  l'ordre         (le "dans quel ordre")
catalog.yml →  les données     (le "avec quoi")
params.yml  →  les valeurs     (le "avec quelles valeurs")
```

### Le Data Catalog en pratique

Dans `conf/base/catalog.yml` je déclare une fois :

```yaml
raw_traffic:
  type: spark.SparkDataset
  filepath: data/02_intermediate/raw_traffic/
  file_format: parquet
```

Et dans mon code je n'écris jamais `spark.read.parquet(...)`. Kedro injecte `raw_traffic` directement dans la fonction. Ça veut dire que si un jour je déplace les données, je change une ligne dans le YAML — le code Python ne change pas.

### `hooks.py` — le code qui tourne avant le pipeline

C'est là que je crée la SparkSession. Kedro l'exécute automatiquement avant de lancer quoi que ce soit.

```python
class SparkHooks:
    @hook_impl
    def after_context_created(self, context) -> None:
        SparkSession.builder \
            .appName("NetSentinel") \
            .config("spark.driver.memory", "25g") \
            .config("spark.executor.memory", "12g") \
            ...
            .getOrCreate()
```

Sans ça, chaque node devrait créer sa propre SparkSession — et Spark ne peut en avoir qu'une active à la fois.

---

## 2. DVC — versionner les données

### Pourquoi

Git ne peut pas versionner des fichiers de 2GB. Mes CSV CIC-IDS2018 font plusieurs centaines de Mo. Sans DVC, soit je les commit quand même (le repo explose), soit je les ignore (un collègue clone et n'a aucune donnée).

DVC résout ça : Git stocke un fichier `.dvc` de quelques lignes (juste un hash), les vraies données sont sur un stockage externe (ici Google Drive).

```
Git voit :     data/01_raw.dvc   ← 5 lignes, commité
DVC stocke :   les vrais CSV     ← sur Google Drive, jamais dans Git
```

### Ce que j'ai configuré

```bash
dvc init
dvc remote add -d gdrive gdrive://1_MEXhcFuAiTRzsD9R46e4ot_1__SdCC8
dvc remote modify gdrive gdrive_client_id "..."
dvc remote modify --local gdrive gdrive_client_secret "..."   # --local = jamais commité
dvc add data/01_raw/
dvc push
```

Le `--local` sur `client_secret` est important : le secret OAuth ne part jamais sur Git, il reste dans `conf/local/` qui est dans `.gitignore`.

### Workflow au quotidien

```bash
# Je modifie les données
dvc add data/01_raw/          # recalcule le hash
git add data/01_raw.dvc
git commit -m "data: nouvelles captures"
dvc push                      # upload sur Google Drive

# Un collègue veut reproduire
git clone <repo>
dvc pull                      # télécharge exactement la même version des données
kedro run                     # et le pipeline tourne
```

C'est ça la reproductibilité : dans 6 mois, je sais exactement sur quelles données mon modèle a été entraîné.

---

## 3. dlt — une couche d'ingestion propre

### Pourquoi

`spark.read.csv(...)` directement dans le pipeline c'est fragile. Pas de validation de schéma, pas de gestion des types, pas de traçabilité. Si une colonne change de nom dans un nouveau fichier CSV → ça plante silencieusement dans Spark 2h plus tard, au milieu de l'entraînement.

dlt (data load tool) règle ça. Il lit les CSV, valide le schéma automatiquement, et écrit du Parquet propre. Si quelque chose cloche → il plante immédiatement avec un message clair.

### Architecture

```
data/01_raw/*.csv
      ↓
[dlt pipeline]        ← valide schéma, lit CSV, écrit Parquet
      ↓
data/02_intermediate/
      ↓
[Kedro pipeline]      ← Spark lit depuis là, proprement
```

### Les 3 concepts clés de dlt

**`@dlt.source`** → déclare d'où viennent les données (un dossier, une API, une DB...)

**`@dlt.resource`** → une table individuelle dans la source (ex: tous les CSV d'un dossier)

**`pipeline.run()`** → l'action qui déclenche tout : lecture + validation + écriture

### Ce que j'ai implémenté

Dans `src/netsentinel/ingestion/dlt_pipeline.py` :

```python
@dlt.source
def cic_ids_source(data_dir):
    return cic_csv_files(data_dir)

@dlt.resource(write_disposition="replace")
def cic_csv_files(data_dir):
    for f in Path(data_dir).glob("*.csv"):
        yield pd.read_csv(f)
```

dlt infère le schéma automatiquement et écrit en Parquet dans `data/02_intermediate/`. Le node Kedro d'ingestion appelle ce pipeline.

### Avant vs après

| Sans dlt | Avec dlt |
|---|---|
| `spark.read.csv(...)` hardcodé | Source déclarée, réutilisable |
| Erreur de schéma → plante dans Spark | Erreur détectée à l'ingestion |
| Pas de logs d'ingestion | lignes chargées, fichiers, erreurs |
| CSV bruts → Spark directement | CSV → Parquet optimisé → Spark |

---

## 4. Apache Spark — le calcul distribué

### Pourquoi

630 000 lignes × 80 features × 80 entraînements (CrossValidation 5 folds × 16 combinaisons) → impossible à faire avec scikit-learn sur un seul thread en temps raisonnable.

Spark distribue tout ça sur les cœurs disponibles. Sur ma machine, il utilise les 22 cores en parallèle. Sur un vrai cluster, il se répartit sur des dizaines de machines.

### Ce que j'utilise dans le pipeline

| Composant | Rôle |
|---|---|
| `StringIndexer` | Convertit les labels texte ("DoS_Hulk") en indices numériques — Spark ML ne travaille qu'avec des nombres |
| `VectorAssembler` | Regroupe toutes les features dans un seul vecteur — format obligatoire pour tous les algos Spark ML |
| `RandomForestClassifier` | Modèle de classification multi-classe distribué |
| `CrossValidator` | Teste les 16 combinaisons d'hyperparamètres, 5 fois chacune = 80 entraînements |
| `ParamGridBuilder` | Construit la grille : `[30, 50, 75, 100]` numTrees × `[5, 8, 10, 11]` maxDepth |
| `MulticlassClassificationEvaluator` | F1-score pondéré — adapté aux classes déséquilibrées |

### Le CrossValidator + l'ensemble

Le CV trouve les meilleurs hyperparamètres. Mais un seul modèle c'est fragile. Alors j'entraîne **10 RandomForest** avec ces meilleurs params mais des seeds différentes (`seed + i`).

Chaque modèle voit les données légèrement différemment (randomisation des arbres). Les prédictions finales sont obtenues par **vote majoritaire** — plus robuste qu'un seul modèle, et ça réduit la variance.

### La config mémoire Spark — pourquoi ces valeurs

```python
.config("spark.driver.memory", "25g")          # le driver coordonne tout
.config("spark.executor.memory", "12g")        # les workers qui calculent
.config("spark.executor.memoryOverhead", "2g") # mémoire JVM hors-heap
.config("spark.driver.maxResultSize", "4g")    # taille max des résultats renvoyés au driver
.config("spark.sql.shuffle.partitions", "4")   # 4 partitions vu qu'on est en local
```

J'avais commencé avec `4g` pour le driver — ça plantait en `Java heap space OutOfMemoryError` pendant la CrossValidation (80 modèles RF en mémoire simultanément). Après avoir vu que mon notebook tournait correctement avec 25g/12g, j'ai repris cette config.

---

## 5. MLflow + kedro-mlflow — tout tracer automatiquement

### Pourquoi

Sans tracking, après 5 runs je ne sais plus :
- Quel run a les meilleures métriques ?
- Avec quels hyperparamètres ?
- Sur quelles données exactement ?
- Où est le fichier du modèle ?

MLflow répond à tout ça.

### kedro-mlflow — le plugin qui connecte les deux

```bash
pip install kedro-mlflow
kedro mlflow init   # génère conf/base/mlflow.yml
```

C'est tout. kedro-mlflow s'enregistre comme hook automatiquement. Chaque `kedro run` crée un run MLflow avec tous les paramètres des fichiers YAML loggués sans que j'écrive une seule ligne de code supplémentaire.

### Ce que MLflow track automatiquement dans l'UI

- **Parameters** : tous mes `parameters.yml` (grille CV, best_params, n_models, test_size...)
- **Metrics** : accuracy, F1, precision, recall — loggués explicitement dans `evaluate_ensemble`
- **Datasets** : le `train_set` avec son hash — je sais exactement sur quelles données le modèle a été entraîné
- **Artifacts** : les 11 modèles binaires Spark sérialisés

### Le tracking du dataset d'entraînement

J'ai ajouté ça dans `train_cross_validator` :

```python
dataset = mlflow.data.from_spark(
    train_set,
    path="data/05_model_input/train.parquet",
    name="train_set"
)
mlflow.log_input(dataset, context="training")
```

MLflow calcule un **hash** du dataset. Dans 6 mois si le modèle se dégrade en prod, je vais dans MLflow, je regarde le hash du dataset d'entraînement, je compare avec les nouvelles données → je vois immédiatement si les données ont changé.

---

## 6. MLflow Model Registry — le cycle de vie du modèle

### Pourquoi

Logguer un modèle dans un run c'est bien. Mais si j'ai 20 runs, lequel est le bon ? Le Model Registry répond à ça : c'est un catalogue global avec des versions et des stages.

### Le cycle de vie

```
Entraînement → Staging → Production → Archived
```

- **Staging** : modèle fraîchement entraîné, en attente de validation
- **Production** : validé, celui qu'on charge en prod
- **Archived** : ancienne version, gardée pour référence

### Ce que j'ai codé dans `train_cross_validator`

```python
# 1. je sauvegarde le meilleur modèle CV comme artefact
mlflow.spark.log_model(cv_model.bestModel, artifact_path="best_cv_model")

# 2. je l'enregistre dans le Registry sous un nom fixe
registered = mlflow.register_model(
    model_uri=f"runs:/{run_id}/best_cv_model",
    name="netsentinel-ids"
)

# 3. je le passe en Staging automatiquement
client = MlflowClient()
client.transition_model_version_stage(
    name="netsentinel-ids",
    version=registered.version,
    stage="Staging",
)
```

### La différence entre log_model et register_model

| `mlflow.spark.log_model()` | `mlflow.register_model()` |
|---|---|
| Sauvegarde dans un run spécifique | Enregistre dans le catalogue global |
| Lié à un run_id | Accessible par nom + version |
| `runs:/abc123/model` | `models:/netsentinel-ids/Production` |

Pour charger le modèle en prod depuis n'importe où :
```python
model = mlflow.spark.load_model("models:/netsentinel-ids/Production")
```

---

## 7. Mise en production sur Windows — les problèmes rencontrés et comment je les ai résolus

C'est la partie qui m'a pris le plus de temps. Le pipeline tournait parfaitement en notebook, mais `kedro run` plantait à chaque étape. Voilà ce que j'ai résolu, dans l'ordre.

---

### Problème 1 — `OutOfMemoryError: Java heap space` pendant la CrossValidation

**Ce qui se passait :** La CrossValidation (80 runs RF) vidait entièrement la mémoire JVM du driver.

**Pourquoi :** La config par défaut de Spark donne 4g au driver. C'est largement insuffisant pour garder 80 modèles RandomForest en mémoire + tout ce que le driver fait (broadcast des données, collecte des métriques...).

**Fix :** J'ai repris exactement la config mémoire qui fonctionnait dans mon notebook :

```python
.config("spark.driver.memory", "25g")
.config("spark.executor.memory", "12g")
.config("spark.executor.memoryOverhead", "2g")
.config("spark.driver.maxResultSize", "4g")
.config("spark.sql.shuffle.partitions", "4")
```

---

### Problème 2 — `cannot pickle '_thread.RLock'` au moment de sauvegarder les modèles dans Kedro

**Ce qui se passait :** Kedro essayait de faire un `copy.deepcopy()` sur le `CrossValidatorModel` (un objet JVM) pour le passer entre les nodes. Les objets JVM contiennent des verrous de thread non-sérialisables.

**Fix :** Dans `conf/base/catalog.yml`, j'ai ajouté `copy_mode: assign` sur les deux datasets en mémoire qui contiennent des modèles Spark :

```yaml
cv_model:
  type: MemoryDataset
  copy_mode: assign    # ← ne pas deepcopy, juste passer la référence

ensemble_models:
  type: MemoryDataset
  copy_mode: assign
```

`copy_mode: assign` dit à Kedro "passe juste la référence directement, ne tente pas de copier l'objet". Parfait pour les objets JVM.

---

### Problème 3 — `UnsatisfiedLinkError: access0` quand Spark essaie d'écrire des fichiers sur Windows

**Ce qui se passait :** Quand `mlflow.spark.log_model()` essayait de sauvegarder le modèle sur le disque, Java cherchait à vérifier les permissions du fichier via `NativeIO$Windows.access0()` — une fonction native dans `hadoop.dll`.

**Le problème en détail :**
- PySpark 4.1.1 embarque des JARs Hadoop **3.4.2**
- La fonction `access0` a une signature JNI différente entre Hadoop 3.3.x et 3.4.x
- J'avais `C:\hadoop\bin\hadoop.dll` version **3.3.6** → signature incompatible → crash

**Ce que j'ai essayé qui n'a pas marché :**
- Supprimer le DLL → Java ne le trouve pas, tombe en fallback Java-only → ça aurait dû marcher mais Java trouvait quand même une version 3.3.6 quelque part
- Renommer en `.bak` → pareil
- Modifier le PATH en Python → Python peut modifier `os.environ["PATH"]` mais Java était déjà initialisé avant que nos modifications prennent effet

**Fix définitif :**

Étape 1 — J'ai mis `winutils.exe` et `hadoop.dll` **3.4.0** dans `bin/hadoop/bin/` (dans le projet, commité dans Git).

Étape 2 — Dans `hooks.py`, j'ai modifié le PATH pour que **notre** `bin/hadoop/bin/` soit en **tête** de PATH, avant tout autre répertoire :

```python
hadoop_home = Path(__file__).parent.parent.parent / "bin" / "hadoop"
os.environ["HADOOP_HOME"] = str(hadoop_home)
hadoop_bin = str(hadoop_home / "bin")
other_paths = [d for d in os.environ.get("PATH", "").split(";") if "hadoop" not in d.lower()]
os.environ["PATH"] = hadoop_bin + ";" + ";".join(other_paths)
```

**Pourquoi ça marche maintenant :**
- La JVM démarre **après** notre modification du PATH (c'est le `SparkSession.builder.getOrCreate()` dans le hook qui la démarre)
- `System.loadLibrary("hadoop")` cherche dans PATH → trouve `bin/hadoop/bin/hadoop.dll` 3.4.0 en premier
- 3.4.0 est compatible avec les JARs 3.4.2 (même branche, signature `access0` identique)
- `mlflow.spark.log_model()` peut écrire sur disque → tous les artefacts sont sauvés

**Bonus reproductibilité :** `winutils.exe` et `hadoop.dll` sont dans `bin/` commité dans Git. Après un `git clone`, le pipeline Spark fonctionne immédiatement sur Windows sans installer Hadoop séparément.

---

### Problème 4 — `ArrowInvalid: Could not convert DenseVector with type DenseVector` à l'export du dashboard

**Ce qui se passait :** Le node `export_dashboard` transformait le DataFrame Spark en Pandas via `.toPandas()`. La colonne `probability` contient des `DenseVector` (type interne Spark ML). PyArrow (la bibliothèque qui fait la conversion Pandas→Parquet) ne connaît pas ce type.

**Tentative initiale :** J'ai essayé `.cast("array<double>")` — Spark 4.x refuse ce cast direct depuis un `VectorUDT`.

**Fix :** Il faut utiliser la fonction dédiée `vector_to_array` de `pyspark.ml.functions` :

```python
from pyspark.ml.functions import vector_to_array

dashboard_df = predictions.select(
    "label",
    "label_index",
    "prediction",
    vector_to_array(F.col("probability")).alias("probability"),
)
```

`vector_to_array` convertit proprement le `DenseVector` Spark en `Array<Double>` standard → PyArrow sait le sérialiser → le Parquet est écrit correctement.

---

### Résultat final

Après tous ces fixes, `kedro run` tourne de bout en bout :

```
✅ run_dlt_ingestion_node        → ingestion dlt
✅ clean_traffic_node            → nettoyage Spark
✅ prepare_features_node         → StringIndexer + VectorAssembler + split 80/20
✅ train_cross_validator_node    → 80 runs CV → meilleurs hyperparamètres
✅ train_ensemble_node           → 10 RF → artefacts MLflow sauvés
✅ evaluate_ensemble_node        → accuracy 0.9859 | F1 0.9839
✅ export_dashboard_node         → 57 913 lignes → Parquet

Pipeline execution completed successfully in 1887.2 sec.
```

Dans MLflow : run **Finished**, 11 artefacts (`best_cv_model` + `rf_model_0..9`), modèle enregistré dans le Registry `netsentinel-ids`.

---

## 8. Les métriques — pourquoi j'ai ajouté precision et recall

Au départ je ne loggais que `accuracy` et `f1_score`. Mais en production SOC, ce qui compte c'est aussi :

- **Precision** : quand le modèle dit "c'est une attaque", à quelle fréquence il a raison ? (faux positifs)
- **Recall** : parmi toutes les vraies attaques, combien il en détecte ? (faux négatifs)

Un modèle IDS préfère en général un recall élevé (mieux vaut une fausse alerte qu'une vraie attaque ratée).

J'ai ajouté dans `evaluate_ensemble` :

```python
def eval(metric_name):
    return MulticlassClassificationEvaluator(
        labelCol="label_index", predictionCol="prediction", metricName=metric_name
    ).evaluate(cv_preds)

accuracy  = eval("accuracy")
f1        = eval("f1")
precision = eval("weightedPrecision")
recall    = eval("weightedRecall")
```

Les métriques sont **pondérées par classe** (`weightedPrecision`, `weightedRecall`) — ce qui est cohérent avec un dataset déséquilibré où certaines classes sont plus rares.

---

## 9. Dashboard SOC + LangChain + LangSmith

### Le dashboard

Construit avec **Plotly Dash**. Il lit `data/08_reporting/dashboard_export.parquet` (généré par le pipeline Kedro) et affiche :

| Graphique | Question SOC |
|---|---|
| Volume par type d'attaque | Qu'est-ce qui attaque, en quel volume ? |
| F1 / Precision / Recall par classe | Détecte-t-on bien chaque menace ? |
| Matrice de confusion | Qu'est-ce qu'on rate ou confond ? |
| Feature Importance | Sur quels signaux repose la détection ? |
| Scatter Precision vs Recall | Quelle attaque est la plus difficile ? |

Tous les graphiques sont interconnectés — cliquer sur une barre filtre automatiquement tout le reste sur cette classe.

### LangChain + Claude — 3 fonctions IA

J'ai intégré 3 boutons IA dans le dashboard, tous propulsés par **Claude Haiku** via LangChain :

**`analyze_threat()`** — L'analyste sélectionne un type d'attaque. Claude reçoit le F1, Precision, Recall et le nombre de faux négatifs de cette classe, et répond : explication de l'attaque, raisons des performances du modèle, risque opérationnel des faux négatifs.

**`generate_soc_report()`** — D'un clic, Claude génère un briefing CISO structuré : situation globale, menaces critiques (F1 < 98%), points forts, 3 recommandations concrètes. Le rapport est exportable en **PDF** via `fpdf2`.

**`chat_soc()`** — Un panneau flottant 💬 pour poser n'importe quelle question en langage naturel. Claude répond avec le contexte complet du dashboard.

### LangSmith — l'observabilité des LLM

C'est le MLflow des LLM. Chaque appel Claude est automatiquement tracé sur `smith.langchain.com` : prompt exact, réponse, tokens, latence, erreurs.

Activation en 3 lignes dans `threat_analyzer.py` :

```python
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = api_key
os.environ["LANGCHAIN_PROJECT"] = "netsentinel"
```

LangChain intercepte automatiquement tous les appels LLM. Aucune modification du code d'appel nécessaire.

Sans LangSmith : si Claude répond n'importe quoi, je vois juste le résultat final dans le dashboard — impossible de savoir pourquoi. Avec LangSmith : je vois exactement quel prompt a produit quelle réponse, et je peux optimiser.

---

## 10. Workflow complet pour reproduire le projet

```bash
# 1. Cloner le repo
git clone <url>
cd NetSentinel

# 2. Installer les dépendances
pip install -e ".[dev]"

# 3. Récupérer les données depuis Google Drive
dvc pull

# 4. Lancer le pipeline complet (~30 min)
kedro run

# 5. Ouvrir MLflow pour voir les résultats
mlflow ui
# → http://localhost:5000

# 6. Lancer le dashboard SOC
python src/netsentinel/dashboard/dashboard.py
# → http://localhost:8050
```

C'est tout. Un `git clone` + `dvc pull` + `kedro run` et on retrouve exactement le même modèle, les mêmes métriques, les mêmes artefacts.

---

## Synthèse des outils

| Outil | Couche | Problème résolu |
|---|---|---|
| **DVC** | Données | Versionner les CSV lourds sur Google Drive |
| **dlt** | Ingestion | Valider le schéma avant que ça arrive dans Spark |
| **Kedro** | Orchestration | Structurer le pipeline en DAG reproductible |
| **Apache Spark** | Calcul | CrossValidation 80 runs + ensemble 10 modèles |
| **MLflow** | Tracking | Tracer params, données, métriques, artefacts |
| **kedro-mlflow** | Intégration | Hook automatique Kedro → MLflow |
| **Model Registry** | Déploiement | Versionnage et cycle de vie du modèle |
| **Plotly Dash** | Visualisation | Dashboard SOC interactif |
| **LangChain + Claude** | IA | Analyse des menaces en langage naturel |
| **LangSmith** | Observabilité | Traçabilité de tous les appels LLM |
| **fpdf2** | Export | Rapport PDF depuis le dashboard |
| **winutils + hadoop.dll** | Compat. Windows | Spark natif sur Windows sans installation globale |
