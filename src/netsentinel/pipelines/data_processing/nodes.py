import logging
from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from netsentinel.ingestion.dlt_pipeline import run_ingestion

logger = logging.getLogger(__name__)

COLS_TO_DROP = [
    "flow_id", "timestamp", "src_ip", "dst_ip",
    "total_header_bytes", "max_header_bytes", "min_header_bytes", "mean_header_bytes", "std_header_bytes",
    "fwd_total_header_bytes", "fwd_max_header_bytes", "fwd_min_header_bytes", "fwd_mean_header_bytes", "fwd_std_header_bytes",
    "bwd_total_header_bytes", "bwd_max_header_bytes", "bwd_min_header_bytes", "bwd_mean_header_bytes", "bwd_std_header_bytes",
    "avg_fwd_bytes_per_bulk", "avg_fwd_packets_per_bulk", "avg_fwd_bulk_rate",
    "avg_bwd_bytes_per_bulk", "avg_bwd_packets_bulk_rate", "avg_bwd_bulk_rate",
    "fwd_bulk_state_count", "fwd_bulk_total_size", "fwd_bulk_per_packet", "fwd_bulk_duration",
    "bwd_bulk_state_count", "bwd_bulk_total_size", "bwd_bulk_per_packet", "bwd_bulk_duration",
    "ece_flag_counts", "cwr_flag_counts", "fwd_ece_flag_counts", "fwd_cwr_flag_counts",
    "bwd_fin_flag_counts", "bwd_psh_flag_counts", "bwd_urg_flag_counts", "bwd_ece_flag_counts",
    "bwd_syn_flag_counts", "bwd_ack_flag_counts", "bwd_cwr_flag_counts", "bwd_rst_flag_counts",
    "active_min", "active_max", "active_mean", "active_std",
    "idle_min", "idle_max", "idle_mean", "idle_std",
    "subflow_fwd_packets", "subflow_bwd_packets", "subflow_fwd_bytes", "subflow_bwd_bytes",
    "payload_bytes_min", "payload_bytes_variance",
    "fwd_payload_bytes_max", "fwd_payload_bytes_min", "fwd_payload_bytes_variance",
    "bwd_payload_bytes_max", "bwd_payload_bytes_min", "bwd_payload_bytes_variance",
    "packet_IAT_total", "fwd_packets_IAT_max", "fwd_packets_IAT_min", "fwd_packets_IAT_total",
    "bwd_packets_IAT_max", "bwd_packets_IAT_min", "bwd_packets_IAT_total",
    "fwd_fin_flag_counts", "fwd_psh_flag_counts", "fwd_urg_flag_counts",
]


def run_dlt_ingestion(parameters: dict) -> None:
    # je lance le pipeline dlt avant tout — il lit les CSV bruts et produit
    # du Parquet propre dans data/02_intermediate/ que Kedro consommera ensuite
    run_ingestion(
        data_path=parameters["raw_data_path"],
        destination_path="data/02_intermediate/raw_traffic",
    )


def ingest_raw_traffic(raw_traffic: DataFrame):
    df = raw_traffic.filter(F.col("label") != "NULL")
    logger.info(f"Ingestion : {df.count():,} lignes, {df.rdd.getNumPartitions()} partitions")
    return df


def balance_traffic(raw_traffic: DataFrame, parameters: dict):
    limits = parameters["class_limits"]
    seed = parameters["random_seed"]

    benign_df    = raw_traffic.filter(F.col("label") == "Benign").orderBy(F.rand(seed=seed)).limit(limits["Benign"])
    dos_hulk_df  = raw_traffic.filter(F.col("label") == "DoS_Hulk").orderBy(F.rand(seed=seed)).limit(limits["DoS_Hulk"])
    port_scan_df = raw_traffic.filter(F.col("label") == "Port_Scan").orderBy(F.rand(seed=seed)).limit(limits["Port_Scan"])
    attacks_df   = raw_traffic.filter(
        (F.col("label") != "Benign") &
        (F.col("label") != "DoS_Hulk") &
        (F.col("label") != "Port_Scan") &
        (F.col("label") != "NULL")
    )

    balanced = benign_df.union(dos_hulk_df).union(port_scan_df).union(attacks_df)
    logger.info(f"Équilibrage : {balanced.count():,} lignes conservées")
    return balanced


def select_features(balanced_traffic: DataFrame) -> DataFrame:
    df = balanced_traffic.drop(*COLS_TO_DROP)
    if "protocol" in df.columns:
        df = df.drop("protocol")
    logger.info(f"Feature selection : {len(df.columns)} colonnes restantes")
    return df
