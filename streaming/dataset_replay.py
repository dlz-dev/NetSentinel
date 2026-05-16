"""Replay d'attaques depuis le dataset CIC-IDS-2017.

Injecte des échantillons réels de chaque classe dans le dossier predictions,
une attaque toutes les CYCLE_DELAY secondes en rotation.
Le dashboard les affiche comme du trafic live.

Usage:
  python streaming/dataset_replay.py          # boucle infinie
  python streaming/dataset_replay.py --once   # un seul passage
"""
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# HADOOP_HOME + PYSPARK_PYTHON sans espaces (requis par le JVM Spark worker)
_BASE   = Path(__file__).resolve().parent.parent
_HADOOP = str(_BASE / "bin" / "hadoop")
_JCT    = Path(r"C:\ns")

def _ensure_junction():
    if not _JCT.exists():
        import subprocess
        subprocess.run(["cmd", "/c", f"mklink /J {_JCT} {_BASE}"],
                       check=True, capture_output=True)

_ensure_junction()

os.environ.setdefault("HADOOP_HOME", _HADOOP)
if _HADOOP + "\\bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.environ.get("PATH", "") + f";{_HADOOP}\\bin"

_PY = str(_JCT / ".venv" / "Scripts" / "python.exe")
os.environ.setdefault("PYSPARK_PYTHON",        _PY)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", _PY)

import pandas as pd


DATA_SRC          = "data/02_intermediate/raw_traffic/data.parquet"
OUT_DIR           = Path("data/streaming/predictions")
SAMPLES_PER_CLASS = 16
CYCLE_DELAY       = 120
MIN_CLASS_SAMPLES = 500

SPARK_MODEL_PATH = (
    "mlruns/485713841488086295"
    "/3af3af713a074f05b8e7a0c36e32db76"
    "/artifacts/best_cv_model"
    "/sparkml/stages/0_RandomForestClassifier_8d712e0c76cd"
)
TMP_BATCH = "data/streaming/tmp_replay_batch.parquet"

SPARK_FEATURE_COLS = [
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

_FAKE_IPS       = [f"192.168.0.{i}" for i in range(2, 30)]
_FAKE_ATTACKERS = [f"10.0.0.{i}"    for i in range(1, 20)]

_APP_MAP = {
    "DoS_Hulk":         "HTTP",
    "DoS_GoldenEye":    "HTTP",
    "DoS_Slowloris":    "HTTP",
    "DoS_Slowhttptest": "HTTP",
    "DDoS_LOIT":        "HTTP",
    "Port_Scan":        "TCP",
    "FTP-Patator":      "FTP",
    "SSH-Patator":      "SSH",
    "Botnet_ARES":      "TLS",
    "Web_Brute_Force":  "HTTP",
    "Web_XSS":          "HTTP",
    "Benign":           "HTTPS",
}

_spark     = None
_model     = None
_assembler = None
_lmap      = None


def _compute_label_map() -> dict:
    df = pd.read_parquet(DATA_SRC)
    df = df[df["label"] != "NULL"]

    N = 50_000
    parts = [
        df[df["label"] == "Benign"].sample(
            n=min(N, int((df["label"] == "Benign").sum())), random_state=42),
        df[df["label"] == "DoS_Hulk"].sample(
            n=min(N, int((df["label"] == "DoS_Hulk").sum())), random_state=42),
        df[df["label"] == "Port_Scan"].sample(
            n=min(N, int((df["label"] == "Port_Scan").sum())), random_state=42),
        df[~df["label"].isin(["Benign", "DoS_Hulk", "Port_Scan", "NULL"])],
    ]
    balanced = pd.concat(parts, ignore_index=True)
    counts   = balanced["label"].value_counts()
    return {i: label for i, label in enumerate(counts.index)}


def _init_spark():
    global _spark, _model, _assembler, _lmap

    from pyspark.sql import SparkSession
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.classification import RandomForestClassificationModel

    print("[replay] Démarrage Spark…")
    _spark = (
        SparkSession.builder
        .appName("netsentinel_replay")
        .master("local[2]")
        .config("spark.driver.memory", "1g")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )
    _spark.sparkContext.setLogLevel("ERROR")

    _model     = RandomForestClassificationModel.load(SPARK_MODEL_PATH)
    _assembler = VectorAssembler(inputCols=SPARK_FEATURE_COLS, outputCol="features")
    _lmap      = _compute_label_map()
    print(f"[replay] Modèle Spark RF chargé — {len(_lmap)} classes")


def _spark_predict(rows: list) -> list:
    batch = pd.DataFrame([
        {c: float(r.get(c, 0) or 0) for c in SPARK_FEATURE_COLS}
        for r in rows
    ])
    batch.to_parquet(TMP_BATCH, index=False)
    df_s   = _assembler.transform(_spark.read.parquet(TMP_BATCH))
    result = _model.transform(df_s).select("prediction").collect()
    return [_lmap.get(int(r.prediction), f"class_{int(r.prediction)}") for r in result]


def _next_epoch():
    files = sorted(OUT_DIR.glob("epoch_*.csv"))
    if not files:
        return 90000
    try:
        return max(int(files[-1].stem.split("_")[1]) + 1, 90000)
    except Exception:
        return 90000


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _init_spark()

    print("[replay] Chargement du dataset…")
    df = pd.read_parquet(DATA_SRC)

    attack_classes = [l for l in df["label"].unique() if l != "Benign"]
    valid_classes  = [c for c in attack_classes if len(df[df["label"] == c]) >= MIN_CLASS_SAMPLES]

    print(f"[replay] {len(valid_classes)} classes | une attaque toutes les {CYCLE_DELAY}s")
    print(f"[replay] Classes : {valid_classes}\n")

    epoch     = _next_epoch()
    cls_index = 0
    once      = "--once" in sys.argv

    while True:
        cls = valid_classes[cls_index % len(valid_classes)]
        cls_index += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cls_df = df[df["label"] == cls]
        sample = cls_df.sample(
            n=min(SAMPLES_PER_CLASS, len(cls_df)),
            random_state=random.randint(0, 9999),
        )

        rows = []
        for _, srow in sample.iterrows():
            rows.append({
                c: float(srow.get(c, 0) or 0)
                for c in SPARK_FEATURE_COLS
                if c in sample.columns
            })
            rows[-1].update({
                "dst_port": int(srow.get("dst_port", 80) or 80),
                "packets_count":       int(srow.get("packets_count", 10) or 10),
                "total_payload_bytes": int(srow.get("total_payload_bytes", 1024) or 1024),
            })

        preds = _spark_predict(rows)

        out_rows = []
        for i, pred in enumerate(preds):
            out_rows.append({
                "label":           pred,
                "src_ip":          random.choice(_FAKE_ATTACKERS),
                "dst_ip":          random.choice(_FAKE_IPS),
                "src_port":        random.randint(1024, 65535),
                "dst_port":        int(sample["dst_port"].iloc[i]) if "dst_port" in sample.columns else 80,
                "app_name":        _APP_MAP.get(cls, "TCP"),
                "hostname":        "",
                "n_pkts":          int(sample["packets_count"].iloc[i]) if "packets_count" in sample.columns else 10,
                "n_bytes":         int(sample["total_payload_bytes"].iloc[i]) if "total_payload_bytes" in sample.columns else 1024,
                "predicted_label": pred,
                "is_attack":       pred != "Benign",
                "detected_at":     now,
            })

        out     = pd.DataFrame(out_rows)
        out.to_csv(OUT_DIR / f"epoch_{epoch:06d}.csv", index=False)
        attacks = int(out["is_attack"].sum())
        labels  = out["predicted_label"].value_counts().to_dict()
        print(f"[replay] {now} | {cls:<20} | {attacks}/{len(out_rows)} détectés | {labels}")
        epoch += 1

        if once:
            break

        print(f"[replay] Prochaine injection dans {CYCLE_DELAY}s…")
        time.sleep(CYCLE_DELAY)


if __name__ == "__main__":
    main()
