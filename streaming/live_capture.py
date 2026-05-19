"""Live network capture — Spark RF pour le trafic temps réel.

Capture le trafic WiFi flux par flux via nfstream, classifie avec le modèle
Spark RF entraîné par Kedro, et publie chaque résultat dans le topic Kafka.

Usage:
  python streaming/live_capture.py
  python streaming/live_capture.py --clean
"""
import json
import sys
from datetime import datetime
from pathlib import Path

# spark_model configure HADOOP_HOME / PYSPARK_PYTHON avant tout import PySpark
import spark_model

import pandas as pd
import yaml
from kafka import KafkaProducer
from nfstream import NFStreamer


def _find_wifi_iface() -> str:
    import re, subprocess
    try:
        out = subprocess.check_output(
            ["tshark", "-D"], text=True, encoding="utf-8", errors="ignore",
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if any(k in line.lower() for k in ("wi-fi", "wireless", "wlan", "wifi")):
                m = re.search(r"(\\Device\\NPF_\{[^}]+\})", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return r"\Device\NPF_{F586232C-3E4E-4E05-87D8-C51F050EF1EF}"


def _streaming_cfg() -> dict:
    raw = yaml.safe_load(Path("conf/base/parameters_streaming.yml").read_text())
    return raw["streaming"]


_CFG       = _streaming_cfg()
IFACE      = _find_wifi_iface()
BATCH_SIZE = _CFG["live_capture"]["batch_size"]
IDLE_T     = _CFG["live_capture"]["idle_timeout"]
ACTIVE_T   = _CFG["live_capture"]["active_timeout"]

print(f"[live] Interface : {IFACE}")

_PROTO_MAP = {6: "TCP", 17: "UDP", 1: "ICMP", 58: "ICMPv6", 132: "SCTP"}
_WELL_KNOWN_PORTS = {
    80: "HTTP", 443: "HTTPS", 53: "DNS", 5353: "mDNS",
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
    110: "POP3", 143: "IMAP", 3389: "RDP", 8080: "HTTP-Alt",
}


def _g(flow, attr):
    return float(getattr(flow, attr, 0) or 0)


def _app_label(flow) -> str:
    raw = str(getattr(flow, "application_name", "") or "")
    if raw and raw not in ("Unknown", "0", ""):
        return raw
    dport = int(getattr(flow, "dst_port", 0) or 0)
    sport = int(getattr(flow, "src_port", 0) or 0)
    return (
        _WELL_KNOWN_PORTS.get(dport)
        or _WELL_KNOWN_PORTS.get(sport)
        or _PROTO_MAP.get(int(getattr(flow, "protocol", 0) or 0), "?")
    )


def _hostname(flow) -> str:
    sni = str(getattr(flow, "requested_server_name", "") or "")
    if sni:
        return sni
    return str(getattr(flow, "dns_query_name", "") or "")


def _flow_to_row(flow) -> dict:
    dur_s = max(_g(flow, "bidirectional_duration_ms") / 1000.0, 1e-6)
    b_fwd = _g(flow, "src2dst_bytes")
    b_bwd = _g(flow, "dst2src_bytes")
    n_bi  = max(_g(flow, "bidirectional_packets"), 1)
    n_fwd = max(_g(flow, "src2dst_packets"), 1)
    n_bwd = max(_g(flow, "dst2src_packets"), 1)

    return {
        "src_ip":   str(flow.src_ip),
        "dst_ip":   str(flow.dst_ip),
        "src_port": _g(flow, "src_port"),
        "dst_port": _g(flow, "dst_port"),
        "app_name": _app_label(flow),
        "hostname": _hostname(flow),
        "n_pkts":   int(_g(flow, "bidirectional_packets")),
        "n_bytes":  int(_g(flow, "bidirectional_bytes")),

        "duration":               _g(flow, "bidirectional_duration_ms"),
        "packets_count":          _g(flow, "bidirectional_packets"),
        "fwd_packets_count":      _g(flow, "src2dst_packets"),
        "bwd_packets_count":      _g(flow, "dst2src_packets"),
        "total_payload_bytes":    _g(flow, "bidirectional_bytes"),
        "fwd_total_payload_bytes": b_fwd,
        "bwd_total_payload_bytes": b_bwd,
        "payload_bytes_max":      _g(flow, "bidirectional_max_ps"),
        "payload_bytes_mean":     _g(flow, "bidirectional_mean_ps"),
        "payload_bytes_std":      _g(flow, "bidirectional_stddev_ps"),
        "fwd_payload_bytes_mean": _g(flow, "src2dst_mean_ps"),
        "fwd_payload_bytes_std":  _g(flow, "src2dst_stddev_ps"),
        "bwd_payload_bytes_mean": _g(flow, "dst2src_mean_ps"),
        "bwd_payload_bytes_std":  _g(flow, "dst2src_stddev_ps"),
        "fwd_avg_segment_size":   b_fwd / n_fwd,
        "bwd_avg_segment_size":   b_bwd / n_bwd,
        "avg_segment_size":       _g(flow, "bidirectional_bytes") / n_bi,
        "fwd_init_win_bytes":     0.0,
        "bwd_init_win_bytes":     0.0,
        "bytes_rate":             _g(flow, "bidirectional_bytes") / dur_s,
        "fwd_bytes_rate":         b_fwd / dur_s,
        "bwd_bytes_rate":         b_bwd / dur_s,
        "packets_rate":           n_bi / dur_s,
        "fwd_packets_rate":       n_fwd / dur_s,
        "bwd_packets_rate":       n_bwd / dur_s,
        "down_up_rate":           b_bwd / max(b_fwd, 1),
        "fin_flag_counts":        _g(flow, "bidirectional_fin_packets"),
        "psh_flag_counts":        _g(flow, "bidirectional_psh_packets"),
        "urg_flag_counts":        _g(flow, "bidirectional_urg_packets"),
        "syn_flag_counts":        _g(flow, "bidirectional_syn_packets"),
        "ack_flag_counts":        _g(flow, "bidirectional_ack_packets"),
        "rst_flag_counts":        _g(flow, "bidirectional_rst_packets"),
        "fwd_syn_flag_counts":    _g(flow, "src2dst_syn_packets"),
        "fwd_ack_flag_counts":    _g(flow, "src2dst_ack_packets"),
        "fwd_rst_flag_counts":    _g(flow, "src2dst_rst_packets"),
        "packets_iat_mean":       _g(flow, "bidirectional_mean_piat_ms"),
        "packet_iat_std":         _g(flow, "bidirectional_stddev_piat_ms"),
        "packet_iat_max":         _g(flow, "bidirectional_max_piat_ms"),
        "packet_iat_min":         _g(flow, "bidirectional_min_piat_ms"),
        "fwd_packets_iat_mean":   _g(flow, "src2dst_mean_piat_ms"),
        "fwd_packets_iat_std":    _g(flow, "src2dst_stddev_piat_ms"),
        "fwd_packets_iat_max":    _g(flow, "src2dst_max_piat_ms"),
        "fwd_packets_iat_min":    _g(flow, "src2dst_min_piat_ms"),
        "bwd_packets_iat_mean":   _g(flow, "dst2src_mean_piat_ms"),
        "bwd_packets_iat_std":    _g(flow, "dst2src_stddev_piat_ms"),
        "bwd_packets_iat_max":    _g(flow, "dst2src_max_piat_ms"),
        "bwd_packets_iat_min":    _g(flow, "dst2src_min_piat_ms"),
    }


def main():
    if "--clean" in sys.argv:
        _pred = Path("data/streaming/predictions")
        if _pred.exists():
            import shutil
            shutil.rmtree(_pred)
            print("[live] Dossier predictions vidé")

    spark_model.init()

    kafka_cfg = _CFG["kafka"]
    producer  = KafkaProducer(
        bootstrap_servers=kafka_cfg["bootstrap_servers"],
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    topic = kafka_cfg["topic"]
    print(f"[live] Capture démarrée → Kafka topic '{topic}'  (Ctrl+C pour arrêter)\n")

    streamer = NFStreamer(
        source=IFACE,
        statistical_analysis=True,
        idle_timeout=IDLE_T,
        active_timeout=ACTIVE_T,
        n_dissections=20,
    )

    buffer = []

    for flow in streamer:
        if ":" in str(flow.src_ip):
            continue
        try:
            buffer.append(_flow_to_row(flow))
        except Exception:
            continue

        if len(buffer) < BATCH_SIZE:
            continue

        try:
            preds = spark_model.predict(buffer)
            now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for row, label in zip(buffer, preds):
                msg = {
                    "_source":         "live",
                    "label":           label,
                    "src_ip":          row["src_ip"],
                    "dst_ip":          row["dst_ip"],
                    "src_port":        int(row["src_port"]),
                    "dst_port":        int(row["dst_port"]),
                    "app_name":        row["app_name"],
                    "hostname":        row["hostname"],
                    "n_pkts":          row["n_pkts"],
                    "n_bytes":         row["n_bytes"],
                    "predicted_label": label,
                    "is_attack":       label != "Benign",
                    "detected_at":     now,
                }
                producer.send(topic, msg)

            atks = sum(1 for p in preds if p != "Benign")
            top  = pd.Series(preds).value_counts().head(3).to_dict()
            print(f"[live] {len(preds)} flows → Kafka | {atks} attacks | {top}")

        except Exception as e:
            print(f"[live] Erreur inférence : {e}")

        buffer = []


if __name__ == "__main__":
    main()
