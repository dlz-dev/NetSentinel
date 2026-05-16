"""NetSentinel — Landing + Live dashboard (port 8060).
Routing: /  → landing page
         /live → live streaming dashboard
"""
import io
import time
from datetime import datetime as _dt
from pathlib import Path

import mlflow
import pandas as pd
import plotly.graph_objects as go
import yaml

import dash
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

# ── Credentials ───────────────────────────────────────────────────────────
with open("conf/local/credentials.yml") as f:
    _creds = yaml.safe_load(f)
ANTHROPIC_KEY = _creds["anthropic"]["api_key"]
LANGSMITH_KEY  = _creds["langsmith"]["api_key"]

# ── Colours ───────────────────────────────────────────────────────────────
BG   = "#050d1a"
CARD = "#0a1628"
A    = "#00d4ff"
R    = "#ff3355"
GR   = "#00e87e"
TX   = "#c2cce0"
DIM  = "#4a6080"
PU   = "#7c5ef7"
OR   = "#ff6b2b"

GL = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
          font=dict(color=DIM, family="JetBrains Mono, monospace", size=10),
          margin=dict(l=8, r=8, t=8, b=8))

_ACOLOR = {
    "DoS_Hulk":          R,
    "DDoS_LOIT":         "#ff1a44",
    "DoS_GoldenEye":     OR,
    "DoS_Slowloris":     "#ff8844",
    "DoS_Slowhttptest":  "#ff7733",
    "Port_Scan":         "#ffa500",
    "Botnet_ARES":       "#cc44ff",
    "FTP-Patator":       "#ff9900",
    "SSH-Patator":       "#ffaa00",
    "Benign":            GR,
}

# ── Simulation cache ──────────────────────────────────────────────────────
import random as _random
_SIM_CACHE: dict = {}
_SIM_APP_MAP = {
    "DoS_Hulk": "HTTP", "DoS_GoldenEye": "HTTP",
    "DoS_Slowloris": "HTTP", "DoS_Slowhttptest": "HTTP",
    "DDoS_LOIT": "HTTP", "Port_Scan": "TCP",
    "FTP-Patator": "FTP", "SSH-Patator": "SSH",
    "Botnet_ARES": "TLS", "Web_XSS": "HTTP",
    "Web_Brute_Force": "HTTP",
}
_FAKE_SRC = [f"10.0.0.{i}" for i in range(1, 20)]
_FAKE_DST = [f"192.168.0.{i}" for i in range(2, 30)]

def _load_sim_cache():
    global _SIM_CACHE
    if _SIM_CACHE:
        return _SIM_CACHE
    try:
        df = pd.read_parquet("data/02_intermediate/raw_traffic/data.parquet")
        for cls in df["label"].unique():
            if cls in ("Benign", "NULL"):
                continue
            sub = df[df["label"] == cls]
            if len(sub) >= 100:
                _SIM_CACHE[cls] = sub
    except Exception:
        pass
    return _SIM_CACHE

def _run_simulation() -> str:
    cache = _load_sim_cache()
    if not cache:
        return "Données non disponibles"
    cls      = _random.choice(list(cache.keys()))
    cls_df   = cache[cls]
    sample   = cls_df.sample(n=min(16, len(cls_df)),
                              random_state=_random.randint(0, 9999))
    files    = sorted(PRED_DIR.glob("epoch_*.csv"))
    rep_files = [f for f in files if int(f.stem.split("_")[1]) >= 90000]
    epoch    = int(rep_files[-1].stem.split("_")[1]) + 1 if rep_files else 90000
    now_s    = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    rows     = []
    for _, row in sample.iterrows():
        rows.append({
            "label":           cls,
            "src_ip":          _random.choice(_FAKE_SRC),
            "dst_ip":          _random.choice(_FAKE_DST),
            "src_port":        _random.randint(1024, 65535),
            "dst_port":        int(row.get("dst_port", 80) or 80),
            "app_name":        _SIM_APP_MAP.get(cls, "TCP"),
            "hostname":        "",
            "n_pkts":          int(row.get("packets_count", 10) or 10),
            "n_bytes":         int(row.get("total_payload_bytes", 1024) or 1024),
            "predicted_label": cls,
            "is_attack":       True,
            "detected_at":     now_s,
        })
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(PRED_DIR / f"epoch_{epoch:06d}.csv", index=False)
    return cls


# ── HTML Report generator ─────────────────────────────────────────────────
_RECO = {
    "DoS_Hulk":         "Implémenter un rate limiting HTTP (nginx/haproxy), activer un WAF. Limiter les connexions simultanées par IP.",
    "DDoS_LOIT":        "Utiliser un CDN avec protection DDoS (Cloudflare, Akamai). Filtrer le trafic en amont via anycast.",
    "DoS_GoldenEye":    "Configurer des timeouts HTTP courts. Limiter les connexions keep-alive par IP.",
    "DoS_Slowloris":    "Utiliser des serveurs événementiels (nginx). Réduire les timeouts client et limiter les slots de connexion.",
    "DoS_Slowhttptest": "Configurer `client_body_timeout` et `client_header_timeout` agressifs sur le serveur web.",
    "Port_Scan":        "Fermer les ports inutilisés. Activer le blocage automatique après N tentatives (iptables, fail2ban).",
    "FTP-Patator":      "Désactiver FTP, migrer vers SFTP avec authentification par clé publique uniquement.",
    "SSH-Patator":      "Déployer fail2ban. Désactiver l'accès SSH root. Forcer l'authentification par clé.",
    "Botnet_ARES":      "Isoler les hôtes compromis du réseau. Analyser les connexions C2 sortantes. Scanner les binaires suspects.",
    "Web_XSS":          "Implémenter Content Security Policy (CSP). Valider et échapper tous les inputs côté serveur.",
    "Web_Brute_Force":  "Ajouter CAPTCHA et 2FA. Implémenter un verrouillage de compte après N échecs.",
    "Web_SQL_Injection":"Utiliser des requêtes préparées (ORM). Auditer toutes les entrées SQL. Désactiver les messages d'erreur verbeux.",
    "Heartbleed":       "Mettre à jour OpenSSL immédiatement (≥ 1.0.1g). Révoquer et regénérer tous les certificats.",
}

def _generate_html_report(ds, _ignored, ai_text):
    now      = _dt.now()
    start_s  = ds.get("session_start")
    total    = ds.get("total", 0)
    attacks  = ds.get("attacks", 0)
    peak     = ds.get("peak_atk", 0)
    top_ip   = ds.get("top_ip", "—") or "—"
    rate     = attacks / total * 100 if total > 0 else 0.0
    counts   = ds.get("class_counts", {})
    rows     = ds.get("recent_rows", [])
    history  = ds.get("history", [])

    if start_s:
        elapsed  = now - _dt.fromisoformat(start_s)
        m, s     = int(elapsed.total_seconds() // 60), int(elapsed.total_seconds() % 60)
        duration = f"{m}m {s}s"
    else:
        duration = "—"

    # Severity
    if rate < 5:   sev, scol = "FAIBLE",    "#00e87e"
    elif rate < 15: sev, scol = "MODÉRÉ",   "#ff6b2b"
    elif rate < 35: sev, scol = "ÉLEVÉ",    "#ff3355"
    else:           sev, scol = "CRITIQUE", "#cc00ff"

    # Top IPs
    atk_src, atk_dst, proto_c, port_c = {}, {}, {}, {}
    total_pkts, total_bytes_atk = 0, 0
    atk_flows = []
    for r in rows:
        if r.get("atk"):
            atk_src[r.get("src","?")] = atk_src.get(r.get("src","?"), 0) + 1
            atk_dst[r.get("dst","?")] = atk_dst.get(r.get("dst","?"), 0) + 1
            proto_c[r.get("app","?")] = proto_c.get(r.get("app","?"), 0) + 1
            p = r.get("dport", 0)
            if p: port_c[p] = port_c.get(p, 0) + 1
            total_pkts      += r.get("pkts", 0)
            total_bytes_atk += r.get("bytes", 0)
            atk_flows.append(r)

    n_atk_flows   = len(atk_flows)
    avg_pkts  = total_pkts / n_atk_flows if n_atk_flows else 0
    avg_bytes = total_bytes_atk / n_atk_flows if n_atk_flows else 0

    top5_src  = sorted(atk_src.items(),  key=lambda x: -x[1])[:5]
    top5_dst  = sorted(atk_dst.items(),  key=lambda x: -x[1])[:5]
    top5_proto= sorted(proto_c.items(),  key=lambda x: -x[1])[:5]
    top5_port = sorted(port_c.items(),   key=lambda x: -x[1])[:5]

    # Attack type table with % and reco
    attack_types = [(k, v) for k, v in sorted(counts.items(), key=lambda x: -x[1]) if k != "Benign"]
    atk_type_rows = ""
    for k, v in attack_types:
        pct  = v / attacks * 100 if attacks else 0
        reco = _RECO.get(k, "Analyser les flux et isoler les sources.")
        atk_type_rows += (
            f"<tr><td><b>{k.replace('_',' ')}</b></td>"
            f"<td style='color:#ff3355'>{v:,}</td>"
            f"<td style='color:#ff6b2b'>{pct:.1f}%</td>"
            f"<td style='color:#6b8099;font-size:10px'>{reco}</td></tr>"
        )
    if not atk_type_rows:
        atk_type_rows = "<tr><td colspan='4' style='color:#4a6080'>Aucune attaque détectée</td></tr>"

    # Sample attack flows (last 20)
    sample_flows = atk_flows[-20:]
    flow_rows = ""
    for r in reversed(sample_flows):
        b = r.get("bytes", 0)
        b_str = f"{b/1024:.1f}K" if b >= 1024 else f"{b}B"
        flow_rows += (
            f"<tr><td>{r.get('t','')}</td>"
            f"<td>{r.get('src','?')}:{r.get('sport','?')}</td>"
            f"<td>{r.get('dst','?')}:{r.get('dport','?')}</td>"
            f"<td>{r.get('app','?')}</td>"
            f"<td>{r.get('pkts','?')} pkt / {b_str}</td>"
            f"<td style='color:#ff3355'><b>{r.get('lbl','?').replace('_',' ')}</b></td></tr>"
        )
    if not flow_rows:
        flow_rows = "<tr><td colspan='6' style='color:#4a6080'>Aucun flux d'attaque enregistré</td></tr>"

    def _ip_rows(lst):
        return "".join(f"<tr><td>{ip}</td><td style='color:#ff3355'>{cnt:,} flux</td></tr>" for ip, cnt in lst) \
               or "<tr><td colspan='2' style='color:#4a6080'>—</td></tr>"

    def _kv_rows(lst):
        return "".join(f"<tr><td>{k}</td><td>{v:,}</td></tr>" for k, v in lst) \
               or "<tr><td colspan='2' style='color:#4a6080'>—</td></tr>"

    # Model info
    f1_val  = round(_F1  * 100, 2)
    acc_val = round(_ACC * 100, 2)
    prec    = round(_METRICS.get("precision_macro", _METRICS.get("precision", _F1)) * 100, 2)
    rec     = round(_METRICS.get("recall_macro",    _METRICS.get("recall",    _F1)) * 100, 2)

    ai_html = ""
    if ai_text and ai_text not in ("En attente d'une analyse…", ""):
        cleaned = str(ai_text).replace("<","&lt;").replace(">","&gt;").replace(chr(10),"<br>")
        ai_html = f"<div class='section'><h2>Analyse IA — Claude Haiku</h2><div class='ai-box'>{cleaned}</div></div>"

    css = """
body{font-family:'JetBrains Mono',monospace;background:#050d1a;color:#c2cce0;margin:48px;line-height:1.5}
h1{color:#00d4ff;border-bottom:2px solid #00d4ff;padding-bottom:10px;margin-bottom:6px;font-size:22px}
h2{color:#00d4ff;font-size:10px;letter-spacing:2px;text-transform:uppercase;margin:28px 0 10px;
   padding:5px 10px;background:rgba(0,212,255,0.05);border-left:3px solid #00d4ff}
.meta{color:#4a6080;font-size:10px;margin-bottom:32px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}
.sc{background:#0a1628;border:1px solid #0d2040;padding:14px;border-radius:6px}
.sv{font-size:24px;font-weight:700;color:#00d4ff}.sl{font-size:9px;color:#4a6080;text-transform:uppercase;letter-spacing:1px;margin-top:3px}
.sev{font-size:20px;font-weight:700}.badge{display:inline-block;padding:4px 14px;border-radius:20px;font-size:11px;font-weight:700;border:1px solid}
table{width:100%;border-collapse:collapse;margin-top:8px}
th{background:#0a1628;color:#00d4ff;padding:8px 12px;text-align:left;font-size:10px;letter-spacing:1px;border-bottom:1px solid #0d2040}
td{padding:6px 12px;border-bottom:1px solid #0d2040;font-size:11px}
tr:last-child td{border-bottom:none}
.ai-box{background:#0a1628;padding:16px;border-radius:6px;border-left:3px solid #00d4ff;font-size:11px;line-height:1.7}
.section{margin-bottom:32px}
.footer{color:#2a3a50;font-size:9px;margin-top:56px;text-align:center;padding-top:16px;border-top:1px solid #0d2040}
@media print{body{background:white;color:#111} h1,h2{color:#0055cc;border-color:#0055cc}
  .sc{border:1px solid #ddd;background:#f5f7fa} .sv{color:#0055cc} table th{background:#e8f0fe;color:#0055cc}}
"""

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>NetSentinel — Rapport d'analyse</title><style>{css}</style></head><body>
<h1>NetSentinel — Rapport d'analyse de sécurité réseau</h1>
<p class='meta'>Généré le <b>{now.strftime('%Y-%m-%d à %H:%M:%S')}</b> &nbsp;·&nbsp; Durée de session : <b>{duration}</b> &nbsp;·&nbsp; Modèle : Spark MLlib RandomForest</p>

<div class='section'><h2>1. Résumé exécutif</h2>
<div class='grid4'>
  <div class='sc'><div class='sv'>{total:,}</div><div class='sl'>Flux analysés</div></div>
  <div class='sc'><div class='sv' style='color:#ff3355'>{attacks:,}</div><div class='sl'>Attaques détectées</div></div>
  <div class='sc'><div class='sv' style='color:#ff6b2b'>{rate:.1f}%</div><div class='sl'>Taux d'attaque</div></div>
  <div class='sc'><div class='sv'>{peak}</div><div class='sl'>Pic flux / tick (1s)</div></div>
</div>
<div class='grid4'>
  <div class='sc'><div class='sv'>{len(attack_types)}</div><div class='sl'>Types d'attaque distincts</div></div>
  <div class='sc'><div class='sv'>{len(atk_src)}</div><div class='sl'>IPs attaquantes uniques</div></div>
  <div class='sc'><div class='sv'>{avg_pkts:.0f}</div><div class='sl'>Paquets moy. / flux attaque</div></div>
  <div class='sc'><div class='sv'>{avg_bytes/1024:.1f}K</div><div class='sl'>Octets moy. / flux attaque</div></div>
</div>
<p style='margin-top:12px'>Niveau de menace global : <span class='badge' style='color:{scol};border-color:{scol};background:rgba(100,100,100,0.1)'>{sev}</span></p>
</div>

<div class='section'><h2>2. Analyse des attaques par type</h2>
<table><tr><th>Type d'attaque</th><th>Flux</th><th>% des attaques</th><th>Recommandation</th></tr>
{atk_type_rows}</table></div>

<div class='section'><h2>3. Activité réseau — Top IPs & Protocoles</h2>
<div class='grid2'>
  <div>
    <b style='font-size:10px;color:#00d4ff'>Top 5 IPs sources (attaquantes)</b>
    <table style='margin-top:6px'><tr><th>IP Source</th><th>Flux</th></tr>{_ip_rows(top5_src)}</table>
  </div>
  <div>
    <b style='font-size:10px;color:#00d4ff'>Top 5 IPs destinations (cibles)</b>
    <table style='margin-top:6px'><tr><th>IP Destination</th><th>Flux</th></tr>{_ip_rows(top5_dst)}</table>
  </div>
</div>
<div class='grid2' style='margin-top:12px'>
  <div>
    <b style='font-size:10px;color:#00d4ff'>Top 5 protocoles applicatifs</b>
    <table style='margin-top:6px'><tr><th>Protocole</th><th>Flux</th></tr>{_kv_rows(top5_proto)}</table>
  </div>
  <div>
    <b style='font-size:10px;color:#00d4ff'>Top 5 ports destination</b>
    <table style='margin-top:6px'><tr><th>Port</th><th>Flux</th></tr>{_kv_rows(top5_port)}</table>
  </div>
</div></div>

<div class='section'><h2>4. Performance du modèle de détection</h2>
<div class='grid4'>
  <div class='sc'><div class='sv' style='color:#00e87e'>{f1_val}%</div><div class='sl'>F1-score macro</div></div>
  <div class='sc'><div class='sv' style='color:#00e87e'>{acc_val}%</div><div class='sl'>Accuracy</div></div>
  <div class='sc'><div class='sv' style='color:#00e87e'>{prec}%</div><div class='sl'>Précision macro</div></div>
  <div class='sc'><div class='sv' style='color:#00e87e'>{rec}%</div><div class='sl'>Rappel macro</div></div>
</div>
<p style='font-size:10px;color:#4a6080;margin-top:10px'>
  Modèle : <b>Spark MLlib RandomForestClassifier</b> · Dataset d'entraînement : <b>CIC-IDS-2017</b> ·
  Features : <b>45 features statistiques de flux réseau</b> · Classes : <b>{_NCLS} classes de trafic</b>
</p></div>

<div class='section'><h2>5. Derniers flux d'attaque détectés (20 derniers)</h2>
<table><tr><th>Heure</th><th>Source</th><th>Destination</th><th>Proto</th><th>Volume</th><th>Label détecté</th></tr>
{flow_rows}</table></div>

{ai_html}

<p class='footer'>NetSentinel · HELMo BLOC2 · Rapport généré automatiquement le {now.strftime('%Y-%m-%d à %H:%M:%S')}<br>
Modèle Spark MLlib · Dataset CIC-IDS-2017 · Pipeline Kedro</p>
</body></html>"""


# ── Startup data ──────────────────────────────────────────────────────────
def _load_mlflow_metrics():
    try:
        mlflow.set_tracking_uri("mlruns")
        runs     = mlflow.search_runs(search_all_experiments=True)
        finished = runs[runs["status"] == "FINISHED"].reset_index(drop=True)
        if not finished.empty:
            row = finished.iloc[0]
            return {k.replace("metrics.", ""): round(float(v), 4)
                    for k, v in row.items()
                    if k.startswith("metrics.") and isinstance(v, float)}
    except Exception:
        pass
    return {}


def _load_dashboard_stats():
    try:
        df = pd.read_parquet("data/08_reporting/dashboard_export.parquet")
        n_classes = df["label"].nunique() if "label" in df.columns else 10
        return len(df), n_classes
    except Exception:
        return 2_800_000, 10


_METRICS      = _load_mlflow_metrics()
_F1           = _METRICS.get("f1_macro", _METRICS.get("f1", 0.97))
_ACC          = _METRICS.get("accuracy", 0.97)
_TOTAL, _NCLS = _load_dashboard_stats()

# ── Predictions reader ────────────────────────────────────────────────────
PRED_DIR = Path("data/streaming/predictions")


def _current_max_epoch() -> int:
    if not PRED_DIR.exists():
        return -1
    files = sorted(PRED_DIR.glob("epoch_*.csv"))
    if not files:
        return -1
    try:
        return int(files[-1].stem.split("_")[1])
    except Exception:
        return -1


def _read_new_epochs(last_live: int, last_replay: int):
    """Read new epoch CSVs from both ranges independently.
    live_capture  → epoch 0-89999
    dataset_replay → epoch 90000+
    """
    if not PRED_DIR.exists():
        return pd.DataFrame(), last_live, last_replay
    files = sorted(PRED_DIR.glob("epoch_*.csv"))
    if not files:
        return pd.DataFrame(), last_live, last_replay

    live_new   = []
    replay_new = []
    for f in files:
        try:
            idx = int(f.stem.split("_")[1])
            if idx < 90000:
                if idx > last_live:
                    live_new.append((idx, f))
            else:
                if idx > last_replay:
                    replay_new.append((idx, f))
        except Exception:
            pass

    # Cap each range to avoid reading too many at once
    live_new   = live_new[-20:]
    replay_new = replay_new[-10:]
    combined   = live_new + replay_new

    if not combined:
        return pd.DataFrame(), last_live, last_replay

    dfs = []
    for _, f in combined:
        try:
            dfs.append(pd.read_csv(f))
        except Exception:
            pass

    if not dfs:
        return pd.DataFrame(), last_live, last_replay

    df             = pd.concat(dfs, ignore_index=True)
    new_last_live  = max((idx for idx, _ in live_new),   default=last_live)
    new_last_replay = max((idx for idx, _ in replay_new), default=last_replay)
    return df, new_last_live, new_last_replay


# ── Dash App ──────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    assets_folder="assets_live",
)
server = app.server

# ════════════════════════════════════════════════════════════════════════════
# LANDING LAYOUT
# ════════════════════════════════════════════════════════════════════════════
def _kpi(label, value, color=A):
    return html.Div([
        html.Div(str(value), className="landing-kpi-value",
                 style={"color": color}),
        html.Div(label, className="landing-kpi-label"),
    ], className="landing-kpi")


landing_layout = html.Div([
    html.Div([
        html.Div("◈", className="landing-icon"),
        html.Div([
            html.Span("Net",      className="brand-net"),
            html.Span("Sentinel", className="brand-sentinel"),
        ], className="landing-title"),
        html.Div("Security Operations Center — IDS Platform",
                 className="landing-subtitle"),

        html.Div([
            _kpi("F1 Macro",    f"{_F1 * 100:.1f}%",    GR),
            _kpi("Accuracy",    f"{_ACC * 100:.1f}%",   GR),
            _kpi("Flux analysés", f"{_TOTAL / 1e6:.1f}M", A),
            _kpi("Classes",     str(_NCLS),              PU),
            _kpi("Modèle",      "RandomForest",          OR),
        ], className="landing-kpis"),

        html.Div([
            html.A([
                html.Div("◈", className="nav-card-icon nav-card-icon-batch"),
                html.Div("Analyse Batch", className="nav-card-title"),
                html.Div("Pipeline Kedro · MLflow · Métriques modèle",
                         className="nav-card-desc"),
            ], href="http://127.0.0.1:8050", target="_blank",
               className="nav-card"),

            html.A([
                html.Div("⚡", className="nav-card-icon"),
                html.Div("Analyse Live", className="nav-card-title"),
                html.Div("nfstream · RF Inference · Alertes temps réel",
                         className="nav-card-desc"),
            ], href="/live", className="nav-card nav-card-live"),
        ], className="nav-cards"),

        html.Div([
            html.Div(className="status-dot"),
            html.Span("Système opérationnel"),
        ], className="landing-status"),

        html.Div("NetSentinel · HELMo BLOC2", className="landing-version"),
    ], className="landing-root"),
], className="page-bg")


# ════════════════════════════════════════════════════════════════════════════
# LIVE LAYOUT (function → fresh stores on each visit)
# ════════════════════════════════════════════════════════════════════════════
def make_live_layout():
    return html.Div([
        # Stores
        dcc.Store(id="data-store",  data={"last_live": -1, "last_replay": 89999,
                                          "history": [], "total": 0, "attacks": 0,
                                          "class_counts": {}, "recent_rows": [],
                                          "session_start": None, "peak_atk": 0,
                                          "top_ip": ""}),
        dcc.Store(id="modal-store",    data={"pending": None, "cooldown": {}}),
        dcc.Store(id="theme-store",    data="dark"),
        dcc.Store(id="feed-qf",        data="all"),
        dcc.Store(id="ai-pending",     data=None),
        dcc.Download(id="pdf-download"),

        # Intervals
        dcc.Interval(id="fast-tick", interval=1_000,  n_intervals=0),
        dcc.Interval(id="slow-tick", interval=3_000,  n_intervals=0),

        # ── Header ──────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.A("← Accueil", href="/", className="back-btn"),
                html.Div([
                    html.Span("Net",      className="brand-net"),
                    html.Span("Sentinel", className="brand-sentinel"),
                    html.Span(" — LIVE",  className="brand-live"),
                ], className="header-brand"),
            ], className="header-left"),
            html.Div([
                html.Button("☀ Clair", id="btn-theme", n_clicks=0,
                            className="theme-btn"),
                html.Button("📥 Rapport", id="btn-export-pdf", n_clicks=0,
                            className="theme-btn"),
                html.Button("⚡ Simuler attaque", id="btn-simulate", n_clicks=0,
                            className="simulate-btn"),
                html.Span("", id="simulate-status", className="simulate-status"),
                html.Div([
                    html.Div(className="live-dot"),
                    html.Span("STREAMING LIVE"),
                ], className="live-badge"),
            ], className="header-right"),
        ], className="live-header"),

        # ── Session stats ─────────────────────────────────────────────────
        html.Div(id="session-stats", className="session-stats"),

        # ── KPIs ────────────────────────────────────────────────────────
        html.Div(id="kpi-row", className="kpi-row"),

        # ── Charts ──────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Div("◈ Flux / seconde", className="card-title"),
                dcc.Graph(id="chart-timeline",
                          config={"scrollZoom": True, "displayModeBar": "hover",
                                  "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]},
                          style={"height": "280px"}),
            ], className="chart-card chart-card--timeline"),

            html.Div([
                html.Div("◈ Classes détectées", className="card-title"),
                dcc.Graph(id="chart-classes",
                          config={"displayModeBar": False},
                          style={"height": "280px"}),
            ], className="chart-card chart-card--classes"),

            html.Div([
                html.Div("◈ Topologie réseau", className="card-title"),
                dcc.Graph(id="chart-topo",
                          config={"displayModeBar": False},
                          style={"height": "280px"}),
            ], className="chart-card chart-card--topo"),
        ], className="charts-row"),

        # ── Feed ────────────────────────────────────────────────────────
        html.Div([
            html.Div("◈ Flux réseau — temps réel", className="card-title"),
            # Filter bar
            html.Div([
                dcc.Input(id="feed-filter", placeholder="Filtrer : IP, port, protocole, host, label…",
                          debounce=True, className="feed-filter-input"),
                html.Div([
                    html.Button("Tout",     id="qf-all",     n_clicks=0, className="qf-btn qf-active"),
                    html.Button("Attaques", id="qf-attacks", n_clicks=0, className="qf-btn"),
                    html.Button("DNS",      id="qf-dns",     n_clicks=0, className="qf-btn"),
                    html.Button("HTTP/S",   id="qf-http",    n_clicks=0, className="qf-btn"),
                    html.Button("TLS",      id="qf-tls",     n_clicks=0, className="qf-btn"),
                    html.Button("QUIC",     id="qf-quic",    n_clicks=0, className="qf-btn"),
                ], className="qf-buttons"),
            ], className="feed-filter-bar"),
            # Header row
            html.Div([
                html.Span("Heure",  className="feed-time   feed-hdr"),
                html.Span("Proto",  className="feed-proto  feed-hdr"),
                html.Span("Source", className="feed-src    feed-hdr"),
                html.Span("",       className="feed-arrow  feed-hdr"),
                html.Span("Dest",   className="feed-dst    feed-hdr"),
                html.Span("Host",   className="feed-host   feed-hdr"),
                html.Span("Taille", className="feed-size   feed-hdr"),
                html.Span("Label",  className="feed-label  feed-hdr"),
            ], className="feed-row feed-header"),
            html.Div(id="feed-table", className="feed-rows"),
        ], className="feed-container"),


        # ── AI Panel ────────────────────────────────────────────────────
        html.Div([
            html.Div("◈ Analyse IA — Claude Haiku",
                     className="card-title"),
            dcc.Markdown(id="ai-text", className="ai-text",
                         children="En attente d'une analyse…"),
        ], id="ai-panel", className="ai-panel",
           style={"display": "none"}),

        # ── Modal ───────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Div("⚠ ALERTE SÉCURITÉ", className="modal-severity"),
                html.Div(id="modal-attack-name", className="modal-attack"),
                html.Div(id="modal-attack-count", className="modal-count"),
                html.Div(
                    "Voulez-vous analyser cette menace avec Claude IA ?",
                    className="modal-question",
                ),
                html.Div([
                    html.Button("Analyser avec Claude",
                                id="btn-yes", n_clicks=0,
                                className="modal-btn modal-btn-yes"),
                    html.Button("Ignorer",
                                id="btn-no", n_clicks=0,
                                className="modal-btn modal-btn-no"),
                ], className="modal-buttons"),
            ], className="modal-box"),
        ], id="modal-container", className="modal-overlay",
           style={"display": "none"}),

    ], id="live-root", className="theme-dark")


# ════════════════════════════════════════════════════════════════════════════
# ROOT LAYOUT + ROUTING
# ════════════════════════════════════════════════════════════════════════════
app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    html.Div(id="page-content"),
], className="page-bg")


@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def display_page(pathname):
    if pathname == "/live":
        return make_live_layout()
    return landing_layout


# ════════════════════════════════════════════════════════════════════════════
# THEME CALLBACKS
# ════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("theme-store", "data"),
    Input("btn-theme", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)
def toggle_theme(n, theme):
    if not n:
        raise PreventUpdate
    return "light" if theme == "dark" else "dark"


@app.callback(
    Output("live-root", "style"),
    Output("live-root", "className"),
    Output("btn-theme", "children"),
    Input("theme-store", "data"),
    prevent_initial_call=True,
)
def apply_theme(theme):
    if theme == "light":
        return (
            {"backgroundColor": "#f0f4f8", "minHeight": "100vh",
             "color": "#1a2a3a", "fontFamily": "JetBrains Mono, monospace"},
            "theme-light",
            "🌙 Sombre",
        )
    return (
        {"backgroundColor": BG, "minHeight": "100vh",
         "color": TX, "fontFamily": "JetBrains Mono, monospace"},
        "theme-dark",
        "☀ Clair",
    )


# ════════════════════════════════════════════════════════════════════════════
# TOPOLOGY BUILDER
# ════════════════════════════════════════════════════════════════════════════

def _network_fig(recent_rows, dim_c, tx_c):
    """Graphe réseau : nœuds IP reliés par des arêtes, attaquants en rouge."""
    _gl_empty = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                     margin=dict(l=8, r=8, t=8, b=8), height=280)

    rows = recent_rows[-200:]
    if not rows:
        fig = go.Figure()
        fig.add_annotation(text="En attente de trafic…", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color=dim_c, size=10))
        fig.update_layout(**_gl_empty)
        return fig

    edge_data    = {}   # (src, dst) -> is_attack
    attacker_set = set()

    for r in rows:
        src = r.get("src", "?")
        dst = r.get("dst", "?")
        atk = bool(r.get("atk", False))
        key = (src, dst)
        edge_data[key] = edge_data.get(key, False) or atk
        if atk:
            attacker_set.add(src)

    all_srcs  = {k[0] for k in edge_data}
    all_dsts  = {k[1] for k in edge_data}
    targets   = sorted((all_dsts - all_srcs) | (all_dsts & attacker_set))[:12]
    attackers = sorted(attacker_set)[:12]
    benign_s  = sorted(all_srcs - attacker_set)[:8]

    def _ys(ips):
        n = len(ips)
        if n == 0: return {}
        if n == 1: return {ips[0]: 0.0}
        return {ip: (i / (n - 1)) * 1.8 - 0.9 for i, ip in enumerate(ips)}

    pos = {}
    for ip, y in _ys(attackers).items(): pos[ip] = (0.05, y)
    for ip, y in _ys(benign_s).items():  pos[ip] = (0.40, y)
    for ip, y in _ys(targets).items():   pos[ip] = (0.95, y)

    bx, by, ax_e, ay_e = [], [], [], []
    for (src, dst), is_atk in edge_data.items():
        if src not in pos or dst not in pos:
            continue
        x0, y0 = pos[src]
        x1, y1 = pos[dst]
        if is_atk:
            ax_e += [x0, x1, None]
            ay_e += [y0, y1, None]
        else:
            bx   += [x0, x1, None]
            by   += [y0, y1, None]

    fig = go.Figure()

    if bx:
        fig.add_trace(go.Scatter(x=bx, y=by, mode="lines",
            line=dict(color="rgba(0,212,255,0.07)", width=1),
            showlegend=False, hoverinfo="skip"))
    if ax_e:
        fig.add_trace(go.Scatter(x=ax_e, y=ay_e, mode="lines",
            line=dict(color="rgba(255,51,85,0.45)", width=1.5),
            showlegend=False, hoverinfo="skip"))

    if attackers:
        a_ips = [ip for ip in attackers if ip in pos]
        fig.add_trace(go.Scatter(
            x=[pos[ip][0] for ip in a_ips], y=[pos[ip][1] for ip in a_ips],
            mode="markers+text",
            marker=dict(color=R, size=10, symbol="diamond",
                        line=dict(color=R, width=1)),
            text=a_ips, textfont=dict(size=7, color=R),
            textposition="middle left", name="Attaquants", hoverinfo="text"))

    if targets:
        t_ips = [ip for ip in targets if ip in pos]
        fig.add_trace(go.Scatter(
            x=[pos[ip][0] for ip in t_ips], y=[pos[ip][1] for ip in t_ips],
            mode="markers+text",
            marker=dict(color=A, size=8, symbol="circle",
                        line=dict(color=A, width=1)),
            text=t_ips, textfont=dict(size=7, color=A),
            textposition="middle right", name="Cibles", hoverinfo="text"))

    if benign_s:
        b_ips = [ip for ip in benign_s if ip in pos]
        fig.add_trace(go.Scatter(
            x=[pos[ip][0] for ip in b_ips], y=[pos[ip][1] for ip in b_ips],
            mode="markers",
            marker=dict(color="#4a6080", size=6, symbol="circle"),
            name="Normal", hovertext=b_ips, hoverinfo="text"))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=80, r=80, t=8, b=30), height=280,
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False,
                   range=[-0.25, 1.25]),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        showlegend=True,
        legend=dict(font=dict(size=8, color=dim_c), x=0.3, y=-0.08,
                    orientation="h"),
        font=dict(color=dim_c, family="JetBrains Mono, monospace", size=8),
        uirevision="network",
    )
    return fig


# ════════════════════════════════════════════════════════════════════════════
# LIVE CALLBACKS
# ════════════════════════════════════════════════════════════════════════════

def _threat_level(rate: float):
    if rate < 0.05:
        return "LOW",      GR,   "▼ LOW"
    if rate < 0.15:
        return "MODERATE", OR,   "◆ MODERATE"
    if rate < 0.35:
        return "HIGH",     R,    "▲ HIGH"
    return "CRITICAL",     "#cc00ff", "⚠ CRITICAL"


def _kpi_card(label, value, color):
    return html.Div([
        html.Div(str(value), className="kpi-value", style={"color": color}),
        html.Div(label, className="kpi-label"),
    ], className="kpi-card",
       style={"border": f"1px solid {color}22"})


# ── Fast: KPIs + data accumulation + modal detection ─────────────────────
@app.callback(
    Output("kpi-row",    "children"),
    Output("data-store", "data"),
    Output("modal-store", "data"),
    Input("fast-tick", "n_intervals"),
    State("data-store",  "data"),
    State("modal-store", "data"),
)
def update_feed(n, ds, ms):
    new_df, new_last_live, new_last_replay = _read_new_epochs(
        ds.get("last_live", -1), ds.get("last_replay", 89999)
    )

    total       = ds["total"]
    attacks     = ds["attacks"]
    class_counts = ds.get("class_counts", {})
    history     = ds.get("history", [])

    new_attack_type = None

    recent_rows = ds.get("recent_rows", [])

    if not new_df.empty:
        if "predicted_label" in new_df.columns:
            n_new_atk = int(new_df["is_attack"].sum()) if "is_attack" in new_df.columns else 0
            total   += len(new_df)
            attacks += n_new_atk

            for lbl, cnt in new_df["predicted_label"].value_counts().items():
                class_counts[lbl] = class_counts.get(lbl, 0) + int(cnt)

            # Propose modal if no attack is already pending and cooldown elapsed
            cooldown = ms.get("cooldown", {})
            pending  = ms.get("pending")
            if not pending:
                now = time.time()
                atk_labels = new_df[new_df["is_attack"] == True]["predicted_label"].unique().tolist() \
                    if "is_attack" in new_df.columns else []
                for lbl in atk_labels:
                    if now - cooldown.get(lbl, 0) > 300:
                        new_attack_type = lbl
                        break

            # Accumulate rows for the persistent feed
            for _, row in new_df.iterrows():
                sport = int(row.get("src_port", 0) or 0)
                dport = int(row.get("dst_port", 0) or 0)
                host  = str(row.get("hostname", "") or "")
                app   = str(row.get("app_name", "") or "")
                recent_rows.append({
                    "t":    str(row.get("detected_at", ""))[-8:],
                    "src":  str(row.get("src_ip", "?"))[:15],
                    "dst":  str(row.get("dst_ip", "?"))[:15],
                    "sport": sport,
                    "dport": dport,
                    "app":  app,
                    "host": host,
                    "pkts": int(row.get("n_pkts", 0) or 0),
                    "bytes": int(row.get("n_bytes", 0) or 0),
                    "lbl":  str(row.get("predicted_label", "?")),
                    "atk":  bool(row.get("is_attack", False)),
                })
            recent_rows = recent_rows[-500:]  # keep last 500 entries

    from datetime import datetime
    top_lbl = ""
    if not new_df.empty and "is_attack" in new_df.columns and "predicted_label" in new_df.columns:
        atk_df = new_df[new_df["is_attack"] == True]
        if not atk_df.empty:
            top_lbl = str(atk_df["predicted_label"].mode().iloc[0])
    history.append({
        "t":     datetime.now().strftime("%H:%M:%S"),
        "total": len(new_df) if not new_df.empty else 0,
        "atk":   int(new_df["is_attack"].sum()) if not new_df.empty and "is_attack" in new_df.columns else 0,
        "top_lbl": top_lbl,
    })
    if len(history) > 60:
        history = history[-60:]

    # KPIs
    rate = attacks / total if total > 0 else 0.0
    _, threat_color, threat_label = _threat_level(rate)

    kpi_row = [
        _kpi_card("Flux analysés",   f"{total:,}",            A),
        _kpi_card("Attaques",        f"{attacks:,}",          R),
        _kpi_card("Taux d'attaque",  f"{rate*100:.1f}%",      OR),
        _kpi_card("Niveau menace",   threat_label,            threat_color),
    ]

    # Session tracking
    session_start = ds.get("session_start")
    if not session_start and not new_df.empty:
        session_start = _dt.now().isoformat()

    peak_atk = ds.get("peak_atk", 0)
    if not new_df.empty and "is_attack" in new_df.columns:
        peak_atk = max(peak_atk, int(new_df["is_attack"].sum()))

    ip_counts = {}
    for r in recent_rows:
        if r.get("atk"):
            ip = r.get("src", "")
            ip_counts[ip] = ip_counts.get(ip, 0) + 1
    top_ip = max(ip_counts, key=ip_counts.get) if ip_counts else ""

    # Update stores
    new_ds = {**ds, "last_live": new_last_live, "last_replay": new_last_replay,
              "total": total, "attacks": attacks, "class_counts": class_counts,
              "history": history, "recent_rows": recent_rows,
              "session_start": session_start, "peak_atk": peak_atk, "top_ip": top_ip}

    new_ms = dict(ms)
    if new_attack_type:
        new_ms["pending"] = new_attack_type

    return kpi_row, new_ds, new_ms


# ── Feed renderer (data-store + filters → feed-table) ────────────────────
def _build_feed_row(r, blocked_ips=None):
    host_disp  = r.get("host") or r.get("app") or ""
    b          = r.get("bytes", 0)
    b_str      = f"{b/1024:.1f}K" if b >= 1024 else f"{b}B"
    is_blocked = blocked_ips and r.get("src") in blocked_ips
    row_cls    = "feed-row feed-row--blocked" if is_blocked else "feed-row"
    return html.Div([
        html.Span(r["t"],                               className="feed-time"),
        html.Span(r.get("app", "?"),                    className="feed-proto"),
        html.Span(f'{r["src"]}:{r.get("sport","?")}',  className="feed-src"),
        html.Span("→",                                  className="feed-arrow"),
        html.Span(f'{r["dst"]}:{r.get("dport","?")}',  className="feed-dst"),
        html.Span(host_disp[:30],                       className="feed-host"),
        html.Span(f'{r.get("pkts","?")}pkt {b_str}',   className="feed-size"),
        html.Span(r["lbl"], className=f"feed-label {'attack' if r.get('atk') else 'benign'}"),
    ], className=row_cls)


@app.callback(
    Output("feed-table", "children"),
    Input("data-store",  "data"),
    Input("feed-filter", "value"),
    Input("feed-qf",     "data"),
)
def render_feed(ds, filter_text, qf):
    rows        = list(ds.get("recent_rows", []))
    blocked_ips = set()
    _KNOWN_QF   = {"all", "attack", "dns", "http", "tls", "quic"}

    # Quick filter
    if qf and qf != "all":
        if qf == "attack":
            rows = [r for r in rows if r.get("atk")]
        elif qf in _KNOWN_QF:
            rows = [r for r in rows if qf.lower() in r.get("app", "").lower()]
        else:
            # Drill-down from chart: match by label
            rows = [r for r in rows if qf.lower() == r.get("lbl", "").lower()]

    # Text filter
    if filter_text and filter_text.strip():
        q = filter_text.strip().lower()
        rows = [r for r in rows if (
            q in r.get("src", "").lower() or
            q in r.get("dst", "").lower() or
            q in r.get("app", "").lower() or
            q in r.get("host", "").lower() or
            q in r.get("lbl", "").lower() or
            q in str(r.get("sport", "")) or
            q in str(r.get("dport", ""))
        )]

    if not rows:
        return [html.Div("Aucun flux correspondant…", className="feed-empty")]
    return [_build_feed_row(r, blocked_ips) for r in reversed(rows)]


# ── Quick filter buttons → feed-qf store ─────────────────────────────────
_QF_IDS = ["qf-all", "qf-attacks", "qf-dns", "qf-http", "qf-tls", "qf-quic"]
_QF_MAP  = {"qf-all": "all", "qf-attacks": "attack", "qf-dns": "dns",
             "qf-http": "http", "qf-tls": "tls", "qf-quic": "quic"}

@app.callback(
    Output("feed-qf",     "data"),
    *[Output(i, "className") for i in _QF_IDS],
    *[Input(i, "n_clicks") for i in _QF_IDS],
    prevent_initial_call=True,
)
def set_quick_filter(*_):
    triggered = dash.ctx.triggered_id or "qf-all"
    active    = _QF_MAP.get(triggered, "all")
    classes   = ["qf-btn qf-active" if i == triggered else "qf-btn" for i in _QF_IDS]
    return active, *classes


# ── Modal show/hide (derived from modal-store) ────────────────────────────
@app.callback(
    Output("modal-container",   "style"),
    Output("modal-attack-name", "children"),
    Output("modal-attack-count","children"),
    Input("modal-store", "data"),
)
def toggle_modal(ms):
    if ms and ms.get("pending"):
        atk   = ms["pending"]
        count = ms.get("seen_count", {}).get(atk, 0)
        return (
            {"display": "flex"},
            atk,
            f"{count} flux détectés" if count else "Attaque en cours",
        )
    return {"display": "none"}, "", ""


# ── Slow: charts + topology ───────────────────────────────────────────────
@app.callback(
    Output("chart-timeline", "figure"),
    Output("chart-classes",  "figure"),
    Output("chart-topo",     "figure"),
    Input("slow-tick",    "n_intervals"),
    Input("theme-store",  "data"),
    State("data-store",   "data"),
)
def update_charts(n, theme, ds):
    history      = ds.get("history", [])
    class_counts = ds.get("class_counts", {})

    is_light = theme == "light"
    dim_c    = "#6b7c93"               if is_light else DIM
    tx_c     = "#1a2a3a"               if is_light else TX
    grid_c   = "rgba(0,0,0,0.07)"     if is_light else "rgba(255,255,255,0.04)"
    rs_bg    = "rgba(0,136,204,0.05)" if is_light else "rgba(0,212,255,0.04)"

    _gl = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
               font=dict(color=dim_c, family="JetBrains Mono, monospace", size=10),
               margin=dict(l=8, r=8, t=8, b=8))

    # Timeline
    fig_t = go.Figure()
    if history:
        times  = [h["t"]     for h in history]
        totals = [h["total"] for h in history]
        atks   = [h["atk"]   for h in history]
        fig_t.add_trace(go.Scatter(
            x=times, y=totals, name="Total",
            line=dict(color=A, width=1.5),
            fill="tozeroy", fillcolor="rgba(0,212,255,0.05)",
            hovertemplate="%{y} flux<extra></extra>",
        ))
        fig_t.add_trace(go.Scatter(
            x=times, y=atks, name="Attaques",
            line=dict(color=R, width=1.5),
            fill="tozeroy", fillcolor="rgba(255,51,85,0.07)",
            hovertemplate="%{y} attaques<extra></extra>",
        ))
        # Attack event markers with label
        atk_h = [h for h in history if h.get("atk", 0) > 0 and h.get("top_lbl")]
        if atk_h:
            fig_t.add_trace(go.Scatter(
                x=[h["t"] for h in atk_h],
                y=[h["total"] for h in atk_h],
                mode="markers+text",
                marker=dict(color=R, size=9, symbol="triangle-up",
                            line=dict(color=R, width=1)),
                text=[h["top_lbl"].replace("_", " ") for h in atk_h],
                textfont=dict(size=7, color=R),
                textposition="top center",
                name="Évènements",
                hovertemplate="<b>%{text}</b><extra></extra>",
                showlegend=False,
            ))
    else:
        fig_t.add_annotation(text="En attente de données…",
                             x=0.5, y=0.5, xref="paper", yref="paper",
                             showarrow=False, font=dict(color=dim_c, size=10))
    fig_t.update_layout(
        **_gl, height=280,
        uirevision="timeline",
        legend=dict(font=dict(size=9), x=0, y=1),
        xaxis=dict(showgrid=False, showticklabels=True, tickfont=dict(size=8),
                   rangeslider=dict(visible=True, thickness=0.07, bgcolor=rs_bg)),
        yaxis=dict(showgrid=True, gridcolor=grid_c, tickfont=dict(size=8)),
    )

    # Class distribution
    fig_c = go.Figure()
    if class_counts:
        sorted_cls = sorted(class_counts.items(), key=lambda x: x[1])
        labels = [k for k, _ in sorted_cls]
        counts = [v for _, v in sorted_cls]
        colors = [_ACOLOR.get(l, dim_c) for l in labels]
        fig_c.add_trace(go.Bar(
            x=counts, y=labels, orientation="h",
            marker=dict(color=colors, opacity=0.85, line=dict(width=0)),
            hovertemplate="<b>%{y}</b>: %{x}<extra></extra>",
        ))
    else:
        fig_c.add_annotation(text="En attente…",
                             x=0.5, y=0.5, xref="paper", yref="paper",
                             showarrow=False, font=dict(color=dim_c, size=10))
    fig_c.update_layout(
        **_gl, height=280,
        xaxis=dict(showgrid=True, gridcolor=grid_c),
        yaxis=dict(showgrid=False, tickfont=dict(size=8, color=tx_c)),
    )

    fig_topo = _network_fig(ds.get("recent_rows", []), dim_c, tx_c)

    return fig_t, fig_c, fig_topo


# ── Button NO: dismiss modal ──────────────────────────────────────────────
@app.callback(
    Output("modal-store", "data", allow_duplicate=True),
    Input("btn-no", "n_clicks"),
    State("modal-store", "data"),
    prevent_initial_call=True,
)
def dismiss_modal(n, ms):
    if not n:
        raise PreventUpdate
    pending  = ms.get("pending")
    cooldown = ms.get("cooldown", {})
    if pending:
        cooldown = {**cooldown, pending: time.time()}
    return {**ms, "pending": None, "cooldown": cooldown}


# ── Button YES : affiche "en cours" immédiatement ────────────────────────
@app.callback(
    Output("modal-store", "data", allow_duplicate=True),
    Output("ai-panel",    "style"),
    Output("ai-text",     "children"),
    Output("ai-pending",  "data"),
    Input("btn-yes", "n_clicks"),
    State("modal-store", "data"),
    prevent_initial_call=True,
)
def show_ai_loading(n, ms):
    if not n:
        raise PreventUpdate
    pending  = ms.get("pending")
    cooldown = ms.get("cooldown", {})
    if pending:
        cooldown = {**cooldown, pending: time.time()}
    new_ms = {**ms, "pending": None, "cooldown": cooldown}
    if not pending:
        return new_ms, {"display": "none"}, "", None

    loading_md = (
        f"### ⏳ Analyse de **{pending}** en cours…\n\n"
        "*Claude Haiku traite les données du flux réseau…*"
    )
    return new_ms, {"display": "block"}, loading_md, pending


# ── Pending store → appel Claude (se déclenche après le 1er callback) ────
@app.callback(
    Output("ai-text",    "children", allow_duplicate=True),
    Output("ai-pending", "data",     allow_duplicate=True),
    Input("ai-pending",  "data"),
    State("data-store",  "data"),
    prevent_initial_call=True,
)
def run_ai_analysis(attack_type, ds):
    if not attack_type:
        raise PreventUpdate
    try:
        from src.netsentinel.agent.threat_analyzer import analyze_threat
        result = analyze_threat(
            attack_type=attack_type,
            f1=_F1 * 100,
            precision=round(_METRICS.get("precision_macro",
                                          _METRICS.get("precision", _F1)) * 100, 2),
            recall=round(_METRICS.get("recall_macro",
                                       _METRICS.get("recall", _F1)) * 100, 2),
            fn=0,
            anthropic_api_key=ANTHROPIC_KEY,
            langsmith_api_key=LANGSMITH_KEY,
        )
    except Exception as e:
        result = f"**Erreur** : {e}"
    return result, None


# ── Feature 4 : Session stats ────────────────────────────────────────────
@app.callback(
    Output("session-stats", "children"),
    Input("slow-tick",  "n_intervals"),
    State("data-store", "data"),
)
def update_session_stats(_, ds):
    start_s = ds.get("session_start")
    if not start_s:
        return [html.Span("Session non démarrée — en attente de trafic…",
                          className="ss-item ss-dim")]
    elapsed  = _dt.now() - _dt.fromisoformat(start_s)
    m, s     = int(elapsed.total_seconds() // 60), int(elapsed.total_seconds() % 60)
    counts   = ds.get("class_counts", {})
    attacks  = {k: v for k, v in counts.items() if k != "Benign"}
    dominant = max(attacks, key=attacks.get).replace("_", " ") if attacks else "—"
    top_ip   = ds.get("top_ip", "") or "—"
    peak     = ds.get("peak_atk", 0)
    return [
        html.Span(f"⏱ {m:02d}m {s:02d}s",           className="ss-item"),
        html.Span("|", className="ss-sep"),
        html.Span(f"Flux : {ds.get('total',0):,}",   className="ss-item"),
        html.Span("|", className="ss-sep"),
        html.Span(f"Pic : {peak} flux/tick",          className="ss-item"),
        html.Span("|", className="ss-sep"),
        html.Span(f"Attaque dominante : {dominant}",
                  className="ss-item ss-attack" if attacks else "ss-item"),
        html.Span("|", className="ss-sep"),
        html.Span(f"IP top : {top_ip}",              className="ss-item"),
    ]


# ── Feature 1 : PDF/HTML export ──────────────────────────────────────────
@app.callback(
    Output("pdf-download", "data"),
    Input("btn-export-pdf", "n_clicks"),
    State("data-store",     "data"),
    State("ai-text",        "children"),
    prevent_initial_call=True,
)
def export_pdf(n, ds, ai_text):
    if not n:
        raise PreventUpdate
    content = _generate_html_report(ds, [], ai_text)
    return dcc.send_bytes(content.encode("utf-8"), "rapport_netsentinel.html")



# ── Simulation ───────────────────────────────────────────────────────────
@app.callback(
    Output("simulate-status", "children"),
    Output("simulate-status", "style"),
    Output("modal-store", "data", allow_duplicate=True),
    Input("btn-simulate", "n_clicks"),
    State("modal-store", "data"),
    prevent_initial_call=True,
)
def simulate_attack(n, ms):
    if not n:
        raise PreventUpdate
    cls = _run_simulation()
    if cls == "Données non disponibles":
        return "⚠ Données parquet introuvables", {"color": "#ff6b2b"}, ms
    # Reset cooldown for this class so the modal fires on the next tick
    new_ms = {**ms, "pending": None,
              "cooldown": {**ms.get("cooldown", {}), cls: 0}}
    col = _ACOLOR.get(cls, "#ff3355")
    return f"⚡ {cls.replace('_', ' ')} injecté", {"color": col}, new_ms


# ── Feature 3 : Drill-down depuis chart-classes ───────────────────────────
@app.callback(
    Output("feed-qf", "data", allow_duplicate=True),
    Input("chart-classes", "clickData"),
    prevent_initial_call=True,
)
def drill_from_chart(click_data):
    if not click_data:
        raise PreventUpdate
    pts = click_data.get("points", [])
    if not pts:
        raise PreventUpdate
    label = str(pts[0].get("y", ""))
    return label if label else "all"


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("NetSentinel → http://127.0.0.1:8060")
    app.run(debug=False, port=8060)
