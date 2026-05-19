"""Spark RF model partagé — chargé une fois, utilisé par live_capture et dataset_replay.

L'import de ce module configure automatiquement l'environnement Windows (HADOOP_HOME,
PYSPARK_PYTHON) avant que PySpark soit importé.
"""
import os
from pathlib import Path

# ── Configuration Windows ──────────────────────────────────────────────────────
_BASE   = Path(__file__).resolve().parent.parent
_HADOOP = str(_BASE / "bin" / "hadoop")
_JCT    = Path(r"C:\ns")


def _ensure_junction():
    if not _JCT.exists():
        import subprocess
        subprocess.run(
            ["cmd", "/c", f"mklink /J {_JCT} {_BASE}"],
            check=True, capture_output=True,
        )


_ensure_junction()
os.environ.setdefault("HADOOP_HOME", _HADOOP)
if _HADOOP + "\\bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.environ.get("PATH", "") + f";{_HADOOP}\\bin"

_PY = str(_JCT / ".venv" / "Scripts" / "python.exe")
os.environ.setdefault("PYSPARK_PYTHON",        _PY)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", _PY)

# ── Constantes ─────────────────────────────────────────────────────────────────
import pandas as pd

SPARK_MODEL_PATH = (
    "mlruns/485713841488086295"
    "/3af3af713a074f05b8e7a0c36e32db76"
    "/artifacts/best_cv_model"
    "/sparkml/stages/0_RandomForestClassifier_8d712e0c76cd"
)
DATA_SRC  = "data/02_intermediate/raw_traffic/data.parquet"
TMP_BATCH = "data/streaming/tmp_spark_batch.parquet"

FEATURE_COLS = [
    "src_port", "dst_port", "duration", "packets_count",
    "fwd_packets_count", "bwd_packets_count",
    "total_payload_bytes", "fwd_total_payload_bytes", "bwd_total_payload_bytes",
    "payload_bytes_max", "payload_bytes_mean", "payload_bytes_std",
    "fwd_payload_bytes_mean", "fwd_payload_bytes_std",
    "bwd_payload_bytes_mean", "bwd_payload_bytes_std",
    "fwd_avg_segment_size", "bwd_avg_segment_size", "avg_segment_size",
    "fwd_init_win_bytes", "bwd_init_win_bytes",
    "bytes_rate", "fwd_bytes_rate", "bwd_bytes_rate",
    "packets_rate", "bwd_packets_rate", "fwd_packets_rate", "down_up_rate",
    "fin_flag_counts", "psh_flag_counts", "urg_flag_counts",
    "syn_flag_counts", "ack_flag_counts", "rst_flag_counts",
    "fwd_syn_flag_counts", "fwd_ack_flag_counts", "fwd_rst_flag_counts",
    "packets_iat_mean", "packet_iat_std", "packet_iat_max", "packet_iat_min",
    "fwd_packets_iat_mean", "fwd_packets_iat_std",
    "bwd_packets_iat_mean", "bwd_packets_iat_std",
]

# ── État global (initialisé une fois par process) ──────────────────────────────
_spark     = None
_model     = None
_assembler = None
_lmap      = None


def _compute_label_map() -> dict:
    df = pd.read_parquet(DATA_SRC)
    df = df[df["label"] != "NULL"]
    N  = 50_000
    balanced = pd.concat([
        df[df["label"] == "Benign"].sample(n=min(N, (df["label"] == "Benign").sum()), random_state=42),
        df[df["label"] == "DoS_Hulk"].sample(n=min(N, (df["label"] == "DoS_Hulk").sum()), random_state=42),
        df[df["label"] == "Port_Scan"].sample(n=min(N, (df["label"] == "Port_Scan").sum()), random_state=42),
        df[~df["label"].isin(["Benign", "DoS_Hulk", "Port_Scan", "NULL"])],
    ], ignore_index=True)
    counts = balanced["label"].value_counts()
    return {i: label for i, label in enumerate(counts.index)}


def init():
    """Démarre la SparkSession et charge le modèle RF. À appeler une seule fois au démarrage."""
    global _spark, _model, _assembler, _lmap

    from pyspark.sql import SparkSession
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.classification import RandomForestClassificationModel

    print("[spark_model] Démarrage Spark…")
    _spark = (
        SparkSession.builder
        .appName("netsentinel_streaming")
        .master("local[2]")
        .config("spark.driver.memory", "1g")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )
    _spark.sparkContext.setLogLevel("ERROR")

    _model     = RandomForestClassificationModel.load(SPARK_MODEL_PATH)
    _assembler = VectorAssembler(inputCols=FEATURE_COLS, outputCol="features")
    _lmap      = _compute_label_map()
    print(f"[spark_model] Modèle RF chargé — {len(_lmap)} classes")


def predict(rows: list) -> list:
    """Classifie une liste de dicts de features. Retourne une liste de labels."""
    Path(TMP_BATCH).parent.mkdir(parents=True, exist_ok=True)
    batch = pd.DataFrame([
        {c: float(r.get(c, 0) or 0) for c in FEATURE_COLS}
        for r in rows
    ])
    batch.to_parquet(TMP_BATCH, index=False)
    df_s   = _assembler.transform(_spark.read.parquet(TMP_BATCH))
    result = _model.transform(df_s).select("prediction").collect()
    return [_lmap.get(int(r.prediction), f"class_{int(r.prediction)}") for r in result]
