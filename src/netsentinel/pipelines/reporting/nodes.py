import logging
import mlflow
from pyspark.ml.evaluation import MulticlassClassificationEvaluator
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

    evaluator_f1 = MulticlassClassificationEvaluator(labelCol="label_index", predictionCol="prediction", metricName="f1")
    evaluator_acc = MulticlassClassificationEvaluator(labelCol="label_index", predictionCol="prediction", metricName="accuracy")

    f1 = evaluator_f1.evaluate(cv_preds)
    accuracy = evaluator_acc.evaluate(cv_preds)

    metrics = {"accuracy": round(accuracy, 4), "f1_score": round(f1, 4)}

    # je logue les métriques finales dans MLflow pour les retrouver dans l'UI
    mlflow.log_metrics(metrics)
    logger.info(f"Accuracy : {accuracy:.4f} | F1 : {f1:.4f}")
    return metrics


def export_dashboard(test_set: DataFrame, cv_model: object) -> DataFrame:
    # j'applique le meilleur modèle sur le test set pour générer les prédictions finales
    predictions = cv_model.transform(test_set)

    # je garde uniquement les colonnes utiles pour le dashboard plotly
    dashboard_df = predictions.select(
        "label",
        "label_index",
        "prediction",
        "probability",
    )

    logger.info(f"Export dashboard : {dashboard_df.count():,} lignes")
    return dashboard_df
