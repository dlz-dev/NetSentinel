import logging
import mlflow
import mlflow.data
import mlflow.spark
from mlflow.tracking import MlflowClient
from pyspark.ml.classification import RandomForestClassifier
from pyspark.ml.evaluation import MulticlassClassificationEvaluator
from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.ml.tuning import CrossValidator, ParamGridBuilder
from pyspark.sql import DataFrame

logger = logging.getLogger(__name__)


def prepare_features(clean_traffic: DataFrame, parameters: dict) -> tuple:
    seed = parameters["random_seed"]
    test_size = parameters["test_size"]

    # j'utilise StringIndexer pour transformer la colonne label (texte) en indices numériques
    # car Spark ML ne travaille qu'avec des nombres — Benign → 0, DoS_Hulk → 1, etc.
    indexer = StringIndexer(inputCol="label", outputCol="label_index", handleInvalid="keep")
    df_indexed = indexer.fit(clean_traffic).transform(clean_traffic)

    # je récupère toutes les colonnes sauf label et label_index pour construire le vecteur de features
    feature_cols = [c for c in df_indexed.columns if c not in ["label", "label_index"]]

    # je regroupe toutes les features dans une seule colonne "features" de type Vector
    # c'est le format attendu par tous les algorithmes ML de Spark
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features")
    df_final = assembler.transform(df_indexed)

    # je sépare le dataset en 80% train et 20% test avec une graine fixe pour la reproductibilité
    train_df, test_df = df_final.randomSplit([1 - test_size, test_size], seed=seed)
    logger.info(f"Train : {train_df.count():,} lignes | Test : {test_df.count():,} lignes")
    return train_df, test_df


def train_cross_validator(train_set: DataFrame, parameters: dict) -> object:
    grid = parameters["cv_grid"]
    seed = parameters["random_seed"]

    # je crée un RandomForestClassifier de base — les hyperparamètres seront testés par le CV
    rf = RandomForestClassifier(labelCol="label_index", featuresCol="features", seed=seed)

    # je construis la grille de recherche : 4 valeurs de numTrees × 4 valeurs de maxDepth = 16 combinaisons
    param_grid = (ParamGridBuilder()
        .addGrid(rf.numTrees, grid["num_trees"])
        .addGrid(rf.maxDepth, grid["max_depth"])
        .build())

    # j'utilise le F1-score comme métrique d'évaluation car le dataset est déséquilibré
    evaluator = MulticlassClassificationEvaluator(labelCol="label_index", metricName="f1")

    # je configure le CrossValidator avec 5 folds — chaque combinaison est testée 5 fois = 80 entraînements au total
    cv = CrossValidator(
        estimator=rf,
        estimatorParamMaps=param_grid,
        evaluator=evaluator,
        numFolds=grid["n_folds"],
        seed=seed,
        parallelism=1,
    )

    # je logue le dataset d'entraînement dans MLflow pour savoir exactement sur quelles données
    # ce modèle a été entraîné — utile si le modèle se dégrade en prod et qu'on veut comparer
    dataset = mlflow.data.from_spark(
        train_set,
        path="data/05_model_input/train.parquet",
        name="train_set"
    )
    mlflow.log_input(dataset, context="training")

    cv_model = cv.fit(train_set)

    best = cv_model.bestModel
    best_f1 = max(cv_model.avgMetrics)
    logger.info(f"CV terminé — F1={best_f1:.4f}, numTrees={best.getNumTrees}, maxDepth={best.getOrDefault('maxDepth')}")

    try:
        run_id = mlflow.active_run().info.run_id
        model_uri = f"runs:/{run_id}/best_cv_model"
        mlflow.spark.log_model(cv_model.bestModel, artifact_path="best_cv_model")
        registered = mlflow.register_model(model_uri=model_uri, name="netsentinel-ids")
        client = MlflowClient()
        client.transition_model_version_stage(
            name="netsentinel-ids",
            version=registered.version,
            stage="Staging",
        )
        logger.info(f"Modèle enregistré dans le registry — version {registered.version} en Staging")
    except Exception as e:
        logger.warning(f"Artefact MLflow non sauvegardé ({e.__class__.__name__}) — params loggués manuellement")
        mlflow.log_params({
            "best_numTrees": best.getNumTrees,
            "best_maxDepth": best.getOrDefault("maxDepth"),
        })
        mlflow.log_metric("cv_best_f1", best_f1)
    return cv_model


def train_ensemble(train_set: DataFrame, parameters: dict) -> list:
    n_models = parameters["n_models"]
    num_trees = parameters["best_params"]["num_trees"]
    max_depth = parameters["best_params"]["max_depth"]
    base_seed = parameters["random_seed"]

    # j'entraîne 10 modèles RandomForest avec les meilleurs hyperparamètres trouvés par le CV
    # chaque modèle a une graine différente pour introduire de la variété — c'est le principe du bagging
    models = []
    for i in range(n_models):
        rf = RandomForestClassifier(
            labelCol="label_index",
            featuresCol="features",
            numTrees=num_trees,
            maxDepth=max_depth,
            seed=base_seed + i,
        )
        model = rf.fit(train_set)
        models.append(model)

        try:
            mlflow.spark.log_model(model, artifact_path=f"rf_model_{i}")
        except Exception as e:
            logger.warning(f"rf_model_{i} non sauvegardé ({e.__class__.__name__})")
        logger.info(f"Modèle {i+1}/{n_models} entraîné")

    return models
