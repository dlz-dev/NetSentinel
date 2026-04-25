import mlflow
from kedro.framework.hooks import hook_impl
from pyspark.sql import SparkSession


class SparkHooks:
    @hook_impl
    def after_context_created(self, context) -> None:
        SparkSession.builder \
            .appName("NetSentinel") \
            .config("spark.driver.memory", "4g") \
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
            .getOrCreate()


class MLflowHooks:
    @hook_impl
    def before_pipeline_run(self, run_params, pipeline, catalog) -> None:
        mlflow.set_experiment("netsentinel")
        mlflow.start_run(run_name=run_params.get("run_name", "pipeline_run"))

    @hook_impl
    def after_pipeline_run(self, run_params, pipeline, catalog) -> None:
        mlflow.end_run()