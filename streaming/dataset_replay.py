"""Replay d'attaques depuis le dataset CIC-IDS-2017.

Injecte des échantillons réels flux par flux dans le topic Kafka netsentinel-flows,
une classe d'attaque toutes les CYCLE_DELAY secondes en rotation.

Usage:
  python streaming/dataset_replay.py          # boucle infinie
  python streaming/dataset_replay.py --once   # un seul passage
"""
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# spark_model configure HADOOP_HOME / PYSPARK_PYTHON avant tout import PySpark
import spark_model

import pandas as pd
import yaml
from kafka import KafkaProducer


def _streaming_cfg() -> dict:
    raw = yaml.safe_load(Path("conf/base/parameters_streaming.yml").read_text())
    return raw["streaming"]


_CFG = _streaming_cfg()

DATA_SRC          = spark_model.DATA_SRC
SAMPLES_PER_CLASS = _CFG["dataset_replay"]["samples_per_class"]
CYCLE_DELAY       = _CFG["dataset_replay"]["cycle_delay"]
MIN_CLASS_SAMPLES = _CFG["dataset_replay"]["min_class_samples"]

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


def main():
    spark_model.init()

    kafka_cfg = _CFG["kafka"]
    producer  = KafkaProducer(
        bootstrap_servers=kafka_cfg["bootstrap_servers"],
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    topic = kafka_cfg["topic"]

    print("[replay] Chargement du dataset…")
    df = pd.read_parquet(DATA_SRC)

    attack_classes = [l for l in df["label"].unique() if l != "Benign"]
    valid_classes  = [c for c in attack_classes if len(df[df["label"] == c]) >= MIN_CLASS_SAMPLES]

    print(f"[replay] {len(valid_classes)} classes | une attaque toutes les {CYCLE_DELAY}s")
    print(f"[replay] → Kafka topic '{topic}'\n")

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

        rows = [
            {c: float(srow.get(c, 0) or 0) for c in spark_model.FEATURE_COLS if c in sample.columns}
            for _, srow in sample.iterrows()
        ]

        preds = spark_model.predict(rows)

        for i, pred in enumerate(preds):
            msg = {
                "_source":         "replay",
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
            }
            producer.send(topic, msg)

        attacks = sum(1 for p in preds if p != "Benign")
        print(f"[replay] {now} | {cls:<20} | {attacks}/{len(preds)} → Kafka")

        if once:
            producer.flush()
            break

        print(f"[replay] Prochaine injection dans {CYCLE_DELAY}s…")
        time.sleep(CYCLE_DELAY)


if __name__ == "__main__":
    main()
