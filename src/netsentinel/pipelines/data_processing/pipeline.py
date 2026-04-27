from kedro.pipeline import Node, Pipeline

from .nodes import run_dlt_ingestion, ingest_raw_traffic, balance_traffic, select_features


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline([
        Node(
            func=run_dlt_ingestion,
            inputs="params:data_processing",
            outputs="raw_traffic",
            name="run_dlt_ingestion_node",
        ),
        Node(
            func=ingest_raw_traffic,
            inputs="raw_traffic",
            outputs="ingested_traffic",
            name="ingest_raw_traffic_node",
        ),
        Node(
            func=balance_traffic,
            inputs=["ingested_traffic", "params:data_processing"],
            outputs="balanced_traffic",
            name="balance_traffic_node",
        ),
        Node(
            func=select_features,
            inputs="balanced_traffic",
            outputs="clean_traffic",
            name="select_features_node",
        ),
    ])
