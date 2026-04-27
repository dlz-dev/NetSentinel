import logging
import mlflow
from pyspark.ml.evaluation import MulticlassClassificationEvaluator
from pyspark.ml.functions import vector_to_array
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

logger = logging.getLogger(__name__)


def evaluate_ensemble(ensemble_models: list, cv_model: object, test_set: DataFrame) -> dict:
    # j'applique chaque modèle de l'ensemble sur le test set pour obtenir 10 prédictions par connexion
    predictions_list = [
        model.transform(test_set).select("label_index", F.col("prediction").alias(f"pred_{i}"))
        for i, model in enumerate(ensemble_models)
    ]

    # je joins toutes les prédictions sur une seule DataFrame pour faire le vote majoritaire
    df_votes = predictions_list[0]
    for i, preds in enumerate(predictions_list[1:], start=1):
        df_votes = df_votes.join(preds.drop("label_index"), on=df_votes["label_index"] == preds["label_index"], how="inner")

    # je calcule le vote majoritaire — la classe prédite par le plus de modèles gagne
    pred_cols = [F.col(f"pred_{i}") for i in range(len(ensemble_models))]
    df_votes = df_votes.withColumn("final_prediction", pred_cols[0])
    for col in pred_cols[1:]:
        df_votes = df_votes.withColumn("final_prediction",
            F.when(col == df_votes["final_prediction"], df_votes["final_prediction"]).otherwise(col))

    # j'utilise aussi le meilleur modèle CV directement pour comparer
    cv_preds = cv_model.transform(test_set)

    def eval(metric_name):
        return MulticlassClassificationEvaluator(
            labelCol="label_index", predictionCol="prediction", metricName=metric_name
        ).evaluate(cv_preds)

    accuracy  = eval("accuracy")
    f1        = eval("f1")
    precision = eval("weightedPrecision")
    recall    = eval("weightedRecall")

    metrics = {
        "accuracy":  round(accuracy,  4),
        "f1_score":  round(f1,        4),
        "precision": round(precision, 4),
        "recall":    round(recall,    4),
    }

    mlflow.log_metrics(metrics)
    logger.info(f"Accuracy : {accuracy:.4f} | F1 : {f1:.4f} | Precision : {precision:.4f} | Recall : {recall:.4f}")
    return metrics


def export_dashboard(test_set: DataFrame, cv_model: object):
    predictions = cv_model.transform(test_set)

    dashboard_df = predictions.select(
        "label",
        "label_index",
        "prediction",
        vector_to_array(F.col("probability")).alias("probability"),
    )

    logger.info(f"Export dashboard : {dashboard_df.count():,} lignes")
    return dashboard_df.toPandas()
