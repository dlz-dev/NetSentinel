import os
import sys
from pathlib import Path

from kedro.framework.hooks import hook_impl
from pyspark.sql import SparkSession


class SparkHooks:
    @hook_impl
    def after_context_created(self, context) -> None:
        if sys.platform == "win32":
            hadoop_home = Path(__file__).parent.parent.parent / "bin" / "hadoop"
            os.environ["HADOOP_HOME"] = str(hadoop_home)
            hadoop_bin = str(hadoop_home / "bin")
            other_paths = [d for d in os.environ.get("PATH", "").split(";") if "hadoop" not in d.lower()]
            os.environ["PATH"] = hadoop_bin + ";" + ";".join(other_paths)

        SparkSession.builder \
            .appName("NetSentinel") \
            .config("spark.driver.memory", "25g") \
            .config("spark.executor.memory", "12g") \
            .config("spark.executor.memoryOverhead", "2g") \
            .config("spark.driver.maxResultSize", "4g") \
            .config("spark.sql.shuffle.partitions", "4") \
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
            .config("spark.hadoop.io.native.lib.available", "false") \
            .config("spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version", "2") \
            .getOrCreate()
