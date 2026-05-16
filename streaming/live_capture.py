"""Live network capture — sklearn RF (rapide) pour le trafic temps réel.

Usage:
  python streaming/live_capture.py
  python streaming/live_capture.py --retrain
  python streaming/live_capture.py --clean
"""
import pickle
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
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


IFACE      = _find_wifi_iface()
OUT_DIR    = Path("data/streaming/predictions")
BATCH_SIZE = 1
IDLE_T     = 2
ACTIVE_T   = 10

print(f"[live] Interface : {IFACE}")

FEATURE_COLS = [
    "src_port", "dst_port", "duration", "packets_count", "fwd_packets_count",
    "bwd_packets_count", "total_payload_bytes", "fwd_total_payload_bytes",
    "bwd_total_payload_bytes", "payload_bytes_max", "payload_bytes_mean",
    "payload_bytes_std", "fwd_payload_bytes_mean", "fwd_payload_bytes_std",
    "bwd_payload_bytes_mean", "bwd_payload_bytes_std", "fwd_avg_segment_size",
    "bwd_avg_segment_size", "avg_segment_size", "fwd_init_win_bytes",
    "bwd_init_win_bytes", "bytes_rate", "fwd_bytes_rate", "bwd_bytes_rate",
    "packets_rate", "bwd_packets_rate", "fwd_packets_rate", "down_up_rate",
    "fin_flag_counts", "psh_flag_counts", "urg_flag_counts", "syn_flag_counts",
    "ack_flag_counts", "rst_flag_counts", "fwd_syn_flag_counts",
    "fwd_ack_flag_counts", "fwd_rst_flag_counts", "packets_iat_mean",
    "packet_iat_std", "packet_iat_max", "packet_iat_min", "packet_iat_total",
    "fwd_packets_iat_mean", "fwd_packets_iat_std", "fwd_packets_iat_max",
    "fwd_packets_iat_min", "fwd_packets_iat_total", "bwd_packets_iat_mean",
    "bwd_packets_iat_std", "bwd_packets_iat_max", "bwd_packets_iat_min",
    "bwd_packets_iat_total",
]

MODEL_CACHE = Path("data/07_model_output/sklearn_rf_live.pkl")
DATA_SRC    = "data/02_intermediate/raw_traffic/data.parquet"


def _train_and_cache():
    from sklearn.ensemble import RandomForestClassifier

    print("[live] Entraînement sklearn RF (~90s)…")
    df = pd.read_parquet(DATA_SRC)

    N_PER_ATTACK = 4_000
    N_BENIGN     = 20_000
    MIN_SAMPLES  = 500

    parts = [
        df[df["label"] == "Benign"].sample(
            n=min(N_BENIGN, int((df["label"] == "Benign").sum())),
            random_state=42,
        )
    ]
    for cls in [l for l in df["label"].unique() if l != "Benign"]:
        cls_df = df[df["label"] == cls]
        if len(cls_df) < MIN_SAMPLES:
            continue
        n = min(N_PER_ATTACK, len(cls_df))
        parts.append(cls_df.sample(n=n, random_state=42))
        print(f"[live]   {cls}: {n} samples")

    data = pd.concat(parts, ignore_index=True)
    del df

    cols = [c for c in FEATURE_COLS if c in data.columns]
    clf  = RandomForestClassifier(n_estimators=200, n_jobs=-1, random_state=42, min_samples_leaf=3)
    clf.fit(data[cols].fillna(0).values, data["label"].values)

    MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_CACHE, "wb") as f:
        pickle.dump((clf, cols), f)

    print(f"[live] Modèle sauvegardé → {MODEL_CACHE}")
    return clf, cols


def _load_model():
    if MODEL_CACHE.exists() and "--retrain" not in sys.argv:
        print("[live] Modèle chargé depuis le cache")
        with open(MODEL_CACHE, "rb") as f:
            return pickle.load(f)
    return _train_and_cache()


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
        "packet_iat_total":       _g(flow, "bidirectional_mean_piat_ms") * max(n_bi - 1, 0),
        "fwd_packets_iat_mean":   _g(flow, "src2dst_mean_piat_ms"),
        "fwd_packets_iat_std":    _g(flow, "src2dst_stddev_piat_ms"),
        "fwd_packets_iat_max":    _g(flow, "src2dst_max_piat_ms"),
        "fwd_packets_iat_min":    _g(flow, "src2dst_min_piat_ms"),
        "fwd_packets_iat_total":  _g(flow, "src2dst_mean_piat_ms") * max(n_fwd - 1, 0),
        "bwd_packets_iat_mean":   _g(flow, "dst2src_mean_piat_ms"),
        "bwd_packets_iat_std":    _g(flow, "dst2src_stddev_piat_ms"),
        "bwd_packets_iat_max":    _g(flow, "dst2src_max_piat_ms"),
        "bwd_packets_iat_min":    _g(flow, "dst2src_min_piat_ms"),
        "bwd_packets_iat_total":  _g(flow, "dst2src_mean_piat_ms") * max(n_bwd - 1, 0),
    }


def _next_epoch():
    files = [f for f in sorted(OUT_DIR.glob("epoch_*.csv"))
             if int(f.stem.split("_")[1]) < 90000]
    if not files:
        return 0
    try:
        return int(files[-1].stem.split("_")[1]) + 1
    except Exception:
        return 0


def main():
    if "--clean" in sys.argv and OUT_DIR.exists():
        import shutil
        shutil.rmtree(OUT_DIR)
        print("[live] Dossier predictions vidé")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model, feat_cols = _load_model()
    print(f"[live] Capture démarrée → {OUT_DIR}  (Ctrl+C pour arrêter)\n")

    streamer = NFStreamer(
        source=IFACE,
        statistical_analysis=True,
        idle_timeout=IDLE_T,
        active_timeout=ACTIVE_T,
        n_dissections=20,
    )

    buffer = []
    epoch  = _next_epoch()

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
            df = pd.DataFrame(buffer)
            X  = df[feat_cols].fillna(0).astype(float).values

            df["predicted_label"] = model.predict(X)
            df["is_attack"]       = df["predicted_label"] != "Benign"
            df["detected_at"]     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df["label"]           = df["predicted_label"]

            out = df[["label", "src_ip", "dst_ip", "src_port", "dst_port",
                       "app_name", "hostname", "n_pkts", "n_bytes",
                       "predicted_label", "is_attack", "detected_at"]]
            out.to_csv(OUT_DIR / f"epoch_{epoch:06d}.csv", index=False)

            atks = int(df["is_attack"].sum())
            top  = df["predicted_label"].value_counts().head(3).to_dict()
            print(f"[live] epoch={epoch:04d} | {len(df)} flows | {atks} attacks | {top}")
            epoch += 1

        except Exception as e:
            print(f"[live] Erreur inférence : {e}")

        buffer = []


if __name__ == "__main__":
    main()
