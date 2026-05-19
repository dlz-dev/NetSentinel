"""Consommateur Kafka — lit le topic netsentinel-flows et écrit les CSV d'époques.

Maintient deux curseurs d'époque indépendants :
  - live    : 0–89 999   (flux WiFi capturés par live_capture.py)
  - replay  : 90 000+    (flux injectés par dataset_replay.py)

Usage:
  python streaming/kafka_consumer.py
"""
import json
from pathlib import Path

import pandas as pd
import yaml
from kafka import KafkaConsumer

OUT_DIR = Path("data/streaming/predictions")


def _kafka_cfg() -> dict:
    raw = yaml.safe_load(Path("conf/base/parameters_streaming.yml").read_text())
    return raw["streaming"]["kafka"]


def _next_epochs() -> tuple[int, int]:
    files = sorted(OUT_DIR.glob("epoch_*.csv"))
    live, replay = 0, 90000
    for f in reversed(files):
        try:
            n = int(f.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        if n >= 90000 and replay == 90000:
            replay = n + 1
        elif n < 90000 and live == 0:
            live = n + 1
        if live > 0 and replay > 90000:
            break
    return live, replay


def main():
    if OUT_DIR.exists():
        import shutil
        shutil.rmtree(OUT_DIR)
        print("[consumer] Prédictions précédentes supprimées")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = _kafka_cfg()

    print(f"[consumer] Connexion → {cfg['bootstrap_servers']} | topic : {cfg['topic']}")
    consumer = KafkaConsumer(
        cfg["topic"],
        bootstrap_servers=cfg["bootstrap_servers"],
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        group_id="netsentinel-dashboard",
    )

    live_epoch, replay_epoch = 0, 90000
    print(f"[consumer] Prêt — curseur live={live_epoch}, replay={replay_epoch}\n")

    for msg in consumer:
        row = msg.value
        source = row.pop("_source", "live")

        if source == "replay":
            path = OUT_DIR / f"epoch_{replay_epoch:06d}.csv"
            replay_epoch += 1
        else:
            path = OUT_DIR / f"epoch_{live_epoch:06d}.csv"
            live_epoch += 1

        pd.DataFrame([row]).to_csv(path, index=False)
        lbl = row.get("predicted_label", "?")
        print(f"[consumer] {source:<6} → {path.name} | {lbl}")


if __name__ == "__main__":
    main()
