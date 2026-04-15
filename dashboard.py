import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash
from dash import dcc, html, Input, Output
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════
try:
    feature_importance = pd.read_parquet("data/dashboard/feature_importance")
    label_counts       = pd.read_parquet("data/dashboard/label_counts")
    metrics            = pd.read_parquet("data/dashboard/metrics")
    conf_matrix        = pd.read_parquet("data/dashboard/conf_matrix")
    per_class          = pd.read_parquet("data/dashboard/per_class_metrics")
except Exception:
    feature_importance = pd.DataFrame({
        'feature': ['Duration','Fwd_Pkt_Len_Max','Bwd_Pkt_Len_Max','Flow_Bytes/s',
                    'Pkt_Len_Var','Fwd_IAT_Mean','Flow_IAT_Max','Init_Win_Fwd',
                    'Subflow_Bwd_Bytes','Min_Seg_Size_Fwd'],
        'importance': [0.185,0.152,0.131,0.112,0.092,0.081,0.073,0.062,0.058,0.041]
    })
    label_counts = pd.DataFrame({
        'label': ['Benign','DoS_Hulk','Port_Scan','DDoS_LOIT','DoS_GoldenEye',
                  'FTP-Patator','DoS_Slowloris','DoS_Slowhttptest','SSH-Patator','Botnet_ARES'],
        'count': [163000,231073,158924,41000,10293,7938,5796,5499,5897,1966]
    })
    metrics = pd.DataFrame({'metric':['Accuracy','F1-Score','Recall','Precision'],
                            'value':[98.81,98.75,98.70,98.80]})
    per_class = pd.DataFrame({
        'label':    ['DoS_Hulk','Port_Scan','DDoS_LOIT','DoS_GoldenEye','FTP-Patator',
                     'DoS_Slowloris','DoS_Slowhttptest','SSH-Patator','Botnet_ARES'],
        'precision':[98.91,99.47,99.23,98.17,97.83,96.51,96.89,99.05,98.92],
        'recall':   [98.85,99.12,99.31,97.91,98.14,96.75,96.52,99.18,98.73],
        'f1':       [98.88,99.29,99.27,98.04,97.98,96.63,96.70,99.11,98.82],
        'tp':       [228940,157830,40850,10210,7780,5710,5320,5840,1942],
        'fp':       [210,95,85,41,32,55,48,18,12],
        'fn':       [265,94,95,82,158,86,179,79,24]
    })
    conf_matrix = pd.DataFrame({
        'label_index':list(range(10)),
        'final_prediction':list(range(10)),
        'count':[163000,231073,158924,41000,10293,7938,5796,5499,5897,1966]
    })

# ── Dérivés ─────────────────────────────────────────────────────────
ATTACK_TYPES  = [l for l in label_counts["label"].tolist() if l != "Benign"]
TOTAL_ATTACKS = int(label_counts[label_counts["label"] != "Benign"]["count"].sum())
TOTAL_FLOWS   = int(label_counts["count"].sum())
TOTAL_FP      = int(per_class["fp"].sum()) if "fp" in per_class.columns else 0
METRICS_DICT  = dict(zip(metrics["metric"], metrics["value"]))
ACCURACY      = METRICS_DICT.get("Accuracy", METRICS_DICT.get("accuracy", 0))
WORST         = per_class.loc[per_class["f1"].idxmin()] if len(per_class) else None

# ═══════════════════════════════════════════════════════════════════════
# PALETTE
# ═══════════════════════════════════════════════════════════════════════
BG = "#060b18"; CARD = "#0c1525"
# Couleurs UI / texte / table — inchangées
A  = "#00d4ff"; PU = "#7c5ef7"; R  = "#ff3355"; OR = "#ff6b2b"
W  = "#ffa500"; Y  = "#ffd700"; GR = "#00e87e"
TX = "#c2cce0"; DM = "#687595"
# Dégradé chaud pour les graphiques : critique → modéré → faible
CH  = "#9b1f2e"   # rouge foncé  — volume élevé / F1 critique
CMH = "#c94a1a"   # rouge-orange — intermédiaire haut
CM  = "#d4711a"   # orange       — intermédiaire
CML = "#c89b20"   # ambre-or     — intermédiaire bas
CL  = "#a89030"   # or terne     — faible volume / bon F1

GL = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
          font=dict(color=DM, family="Inter, JetBrains Mono, monospace", size=10))
GM = dict(l=10, r=10, t=10, b=10)

# ═══════════════════════════════════════════════════════════════════════
# FIGURES — 4 graphiques, chacun répond à une question SOC précise
# ═══════════════════════════════════════════════════════════════════════

def fig_volume(sel=None):
    """Q1 : Qu'est-ce qui attaque et en quel volume ? — label_counts"""
    df = label_counts[label_counts.label != "Benign"].sort_values("count", ascending=True).copy()
    # Dégradé chaud : volume élevé = rouge foncé, faible = ambre
    vmax = df["count"].max()
    colors = [CH if c/vmax > 0.6 else CMH if c/vmax > 0.35 else CM if c/vmax > 0.15 else CML if c/vmax > 0.05 else CL
              for c in df["count"]]
    opacity = [1.0 if (not sel or sel == "ALL" or l == sel) else 0.12
               for l in df["label"]]
    fig = go.Figure(go.Bar(
        x=df["count"], y=df.label, orientation="h",
        marker=dict(color=colors, opacity=opacity, line=dict(width=0)),
        customdata=df[["label","count"]].values,
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]:,} flows<extra></extra>",
    ))
    fig.update_layout(
        **GL, margin=GM, height=340, bargap=0.3,
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   tickformat=",", tickfont=dict(size=9)),
        yaxis=dict(showgrid=False, tickfont=dict(size=10, color=TX)),
    )
    return fig


def fig_f1(sel=None):
    """Q2 : Détecte-t-on bien chaque type de menace ? — per_class F1"""
    df = per_class.sort_values("f1", ascending=True).copy()
    # Dégradé chaud : mauvais F1 = rouge foncé, bon F1 = ambre/or
    colors = [CL if v >= 99 else CML if v >= 97 else CM if v >= 95 else CMH if v >= 93 else CH
              for v in df["f1"]]
    opacity = [1.0 if (not sel or sel == "ALL" or l == sel) else 0.12
               for l in df["label"]]
    fig = go.Figure()
    # Barres de fond (full = 100%)
    fig.add_trace(go.Bar(
        x=[100]*len(df), y=df.label, orientation="h",
        marker=dict(color="rgba(255,255,255,0.04)", line=dict(width=0)),
        showlegend=False, hoverinfo="skip",
    ))
    # Barres F1
    fig.add_trace(go.Bar(
        x=df["f1"], y=df.label, orientation="h", name="F1",
        showlegend=False,
        marker=dict(color=colors, opacity=opacity, line=dict(width=0)),
        customdata=df[["label","f1","precision","recall","fn"]].values,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "F1 : %{customdata[1]:.2f}%<br>"
            "Precision : %{customdata[2]:.2f}%  ·  Recall : %{customdata[3]:.2f}%<br>"
            "Faux négatifs : %{customdata[4]:,}"
            "<extra></extra>"
        ),
    ))
    # Ligne de seuil à 98%
    fig.add_vline(x=98, line=dict(color="rgba(255,51,85,0.4)", width=1.5, dash="dot"))
    fig.add_annotation(x=98, y=-0.6, text="seuil 98%", showarrow=False,
                       font=dict(size=8, color=R), xanchor="center")
    fig.update_layout(
        **GL, margin=dict(l=10, r=10, t=10, b=30), height=340,
        barmode="overlay", bargap=0.3,
        xaxis=dict(showgrid=False, tickfont=dict(size=9),
                   range=[max(0, df["f1"].min() - 3), 100.3], ticksuffix="%"),
        yaxis=dict(showgrid=False, tickfont=dict(size=10, color=TX)),
    )
    return fig


def fig_confusion():
    """Q3 : Qu'est-ce qu'on confond / rate ? — conf_matrix avec vrais noms"""
    all_labels = label_counts["label"].tolist()
    try:
        pivot = conf_matrix.pivot_table(
            index="label_index", columns="final_prediction", values="count", fill_value=0)
        raw  = pivot.values
        z    = np.log1p(raw)
        idx  = [int(i) for i in pivot.index]
        cols = [int(c) for c in pivot.columns]
        labs_y = [all_labels[i] if i < len(all_labels) else str(i) for i in idx]
        labs_x = [all_labels[c] if c < len(all_labels) else str(c) for c in cols]
    except Exception:
        n      = min(len(all_labels), 10)
        raw    = np.eye(n) * 1000
        z      = np.log1p(raw)
        labs_y = all_labels[:n]
        labs_x = all_labels[:n]

    # Annotations : valeurs réelles dans les cellules non-nulles
    annotations = []
    for i in range(len(labs_y)):
        for j in range(len(labs_x)):
            v = int(raw[i, j]) if i < raw.shape[0] and j < raw.shape[1] else 0
            if v > 0:
                txt = f"{v//1000}K" if v >= 1000 else str(v)
                annotations.append(dict(
                    x=labs_x[j], y=labs_y[i], text=txt, showarrow=False,
                    font=dict(size=8, color="rgba(255,255,255,0.8)",
                              family="JetBrains Mono, monospace"),
                ))

    fig = go.Figure(go.Heatmap(
        z=z, x=labs_x, y=labs_y,
        colorscale=[[0, BG],[0.3, CH],[0.65, CM],[1, CL]],
        showscale=False,
        customdata=raw if raw.shape == z.shape else z,
        hovertemplate="<b>Réel : %{y}</b><br><b>Prédit : %{x}</b><br>%{customdata:,} flows<extra></extra>",
    ))
    fig.update_layout(
        **GL,
        margin=dict(l=10, r=10, t=10, b=90),
        height=420,
        annotations=annotations,
        xaxis=dict(showgrid=False, tickfont=dict(size=8, color=TX),
                   tickangle=-40, title="Prédit",
                   title_font=dict(size=9, color=DM)),
        yaxis=dict(showgrid=False, tickfont=dict(size=8, color=TX),
                   title="Réel", title_font=dict(size=9, color=DM),
                   autorange="reversed"),
    )
    return fig


def fig_features():
    """Q4 : Sur quels signaux repose la détection ? — feature_importance"""
    df = feature_importance.head(12).sort_values("importance", ascending=True)
    fig = go.Figure(go.Bar(
        x=df.importance, y=df.feature, orientation="h",
        marker=dict(color=df.importance,
                    colorscale=[[0, CH],[0.5, CM],[1, CL]],
                    line=dict(width=0)),
        hovertemplate="<b>%{y}</b><br>Importance : %{x:.3f}<extra></extra>",
    ))
    fig.update_layout(
        **GL, margin=GM, height=420, bargap=0.28,
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   tickfont=dict(size=9)),
        yaxis=dict(showgrid=False, tickfont=dict(size=10, color=TX)),
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════
# TABLE DRILL-DOWN
# ═══════════════════════════════════════════════════════════════════════
def make_table(sel=None):
    df = per_class.sort_values("f1", ascending=False).copy()
    if sel and sel != "ALL":
        df = df[df.label == sel]

    # Largeurs fixes : col label plus large, reste équitablement réparti
    COL_W = ["22%", "13%", "13%", "13%", "13%", "13%", "13%"]
    BASE  = {"padding":"9px 14px", "fontSize":"11px",
             "borderBottom":"1px solid rgba(255,255,255,0.04)",
             "verticalAlign":"middle"}
    NUM   = {**BASE, "textAlign":"right", "fontFamily":"JetBrains Mono,monospace"}
    HDR   = {**BASE, "color":A, "fontWeight":"600", "fontSize":"10px",
             "letterSpacing":"0.8px", "textTransform":"uppercase",
             "borderBottom":"1px solid rgba(0,212,255,0.2)", "textAlign":"right"}

    cols     = ["TYPE D'ATTAQUE","F1","PRECISION","RECALL","VRAI POS.","FAUX POS.","FAUX NÉG."]
    col_defs = html.Colgroup([html.Col(style={"width": w}) for w in COL_W])
    header   = html.Tr([
        html.Th(c, style={**HDR, "textAlign": "left" if i == 0 else "right"})
        for i, c in enumerate(cols)
    ])
    rows = [html.Tr([
        html.Td(row.label,              style={**BASE, "color":TX, "fontWeight":"500"}),
        html.Td(f"{row.f1:.2f}%",       style={**NUM, "color": GR if row.f1>=99 else A if row.f1>=98 else W if row.f1>=97 else R, "fontWeight":"700"}),
        html.Td(f"{row.precision:.2f}%",style={**NUM, "color": A}),
        html.Td(f"{row.recall:.2f}%",   style={**NUM, "color": OR}),
        html.Td(f"{int(row.tp):,}",     style={**NUM, "color": GR}),
        html.Td(f"{int(row.fp):,}",     style={**NUM, "color": W}),
        html.Td(f"{int(row.fn):,}",     style={**NUM, "color": R}),
    ]) for _, row in df.iterrows()]
    return html.Table([col_defs, header, *rows],
                      style={"width":"100%", "borderCollapse":"collapse", "tableLayout":"fixed"})


# ═══════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "NetSentinel — SOC Dashboard"

app.index_string = '''<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>NetSentinel // SOC</title>
    {%css%}
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *,*::before,*::after { box-sizing:border-box; }
        :root {
            --bg:#060b18; --card:#0c1525; --bord:rgba(32,160,255,0.13);
            --a:#00d4ff; --r:#ff3355; --g:#00e87e; --tx:#c2cce0; --dm:#687595;
            --glow:0 0 22px rgba(0,212,255,0.15);
        }
        html,body { margin:0; padding:0; background:var(--bg); color:var(--tx);
            font-family:'Inter',monospace; overflow-x:hidden; }
        ::-webkit-scrollbar { width:4px; }
        ::-webkit-scrollbar-track { background:var(--bg); }
        ::-webkit-scrollbar-thumb { background:#1a2840; border-radius:2px; }
        ::-webkit-scrollbar-thumb:hover { background:var(--a); }

        /* Header */
        .ns-header {
            display:flex; align-items:center; justify-content:space-between;
            padding:0 28px; height:54px;
            background:rgba(6,11,24,0.97);
            border-bottom:1px solid var(--bord);
            position:sticky; top:0; z-index:100;
        }
        .ns-logo { display:flex; align-items:center; gap:10px; }
        .ns-logo-icon {
            width:32px; height:32px; border-radius:7px;
            background:linear-gradient(135deg,#00d4ff,#0096c7);
            display:flex; align-items:center; justify-content:center;
            font-size:13px; font-weight:700; color:#fff;
        }
        .ns-logo-text { font-size:14px; font-weight:700; letter-spacing:1.5px;
            color:var(--tx); text-transform:uppercase; }
        .ns-logo-text span { color:var(--a); }
        .ns-nav { display:flex; gap:2px; }
        .ns-tab { padding:6px 16px; border-radius:6px; font-size:11px; font-weight:500;
            cursor:pointer; border:none; background:transparent; color:var(--dm);
            transition:all 0.2s; letter-spacing:0.6px; text-transform:uppercase; }
        .ns-tab:hover  { color:var(--tx); background:rgba(255,255,255,0.05); }
        .ns-tab.active { color:var(--a); background:rgba(0,212,255,0.1);
            box-shadow:0 0 0 1px rgba(0,212,255,0.22); }
        .ns-right { display:flex; align-items:center; gap:14px; }
        .badge-live { display:inline-flex; align-items:center; gap:5px; font-size:9px;
            background:rgba(0,232,126,0.1); color:var(--g);
            border:1px solid rgba(0,232,126,0.22); border-radius:20px;
            padding:2px 10px; text-transform:uppercase; letter-spacing:0.5px; }
        .badge-live-dot { width:5px; height:5px; border-radius:50%; background:var(--g);
            animation:blink 1.2s infinite; }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.15} }
        .ns-time { font-size:11px; color:var(--dm); font-family:'JetBrains Mono',monospace; }

        /* KPI */
        .kpi-row { display:grid; grid-template-columns:repeat(4,1fr);
            gap:14px; padding:18px 28px 0; }
        .kpi-card { background:var(--card); border:1px solid var(--bord);
            border-radius:12px; padding:20px 22px;
            display:flex; align-items:center; gap:14px;
            transition:all 0.25s; position:relative; overflow:hidden; }
        .kpi-card::after { content:''; position:absolute; top:0; left:0; right:0; height:2px;
            background:linear-gradient(90deg,transparent,var(--a),transparent);
            opacity:0; transition:opacity 0.3s; }
        .kpi-card:hover::after { opacity:1; }
        .kpi-card:hover { border-color:rgba(0,212,255,0.3); box-shadow:var(--glow); }
        .kpi-icon { width:44px; height:44px; border-radius:10px; flex-shrink:0;
            display:flex; align-items:center; justify-content:center; font-size:20px; }
        .kpi-val { font-size:26px; font-weight:700; letter-spacing:-0.5px; line-height:1.1; }
        .kpi-label { font-size:10px; color:var(--dm); text-transform:uppercase;
            letter-spacing:0.9px; margin-top:3px; }
        .kpi-sub { font-size:9px; color:var(--dm); margin-top:4px; font-family:'JetBrains Mono',monospace; }

        /* Filtre */
        .filter-bar { display:flex; align-items:center; gap:12px;
            padding:12px 28px; border-bottom:1px solid rgba(32,160,255,0.08);
            background:rgba(12,21,37,0.5); }
        .filter-label { font-size:10px; color:var(--dm); text-transform:uppercase;
            letter-spacing:0.9px; font-weight:600; }

        /* Grille principale */
        .grid-2 { display:grid; grid-template-columns:1fr 1fr;
            gap:16px; padding:18px 28px 0; }
        .grid-full { padding:16px 28px 28px; }

        /* Card */
        .ns-card { background:var(--card); border:1px solid var(--bord);
            border-radius:12px; padding:18px 20px; transition:border-color 0.2s; }
        .ns-card:hover { border-color:rgba(0,212,255,0.26); }
        .ns-card-title { font-size:10px; font-weight:600; text-transform:uppercase;
            letter-spacing:1.1px; color:var(--dm); margin-bottom:14px;
            display:flex; align-items:center; gap:8px; }
        .ns-card-title::before { content:''; width:3px; height:12px;
            background:var(--a); border-radius:2px; flex-shrink:0; }
        .ns-card-q { font-size:11px; color:var(--tx); opacity:0.55;
            margin-left:auto; font-style:italic; }

        /* Scanline */
        .scanline { pointer-events:none; position:fixed; top:0; left:0; right:0; height:2px;
            background:linear-gradient(90deg,transparent,rgba(0,212,255,0.08),transparent);
            animation:scan 7s linear infinite; z-index:9999; }
        @keyframes scan { 0%{top:-2px} 100%{top:100vh} }
    </style>
</head>
<body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body>
</html>'''


# ── Helpers layout ───────────────────────────────────────────────────
def kpi(icon, label, value, sub, bg, color):
    return html.Div([
        html.Div(icon, className="kpi-icon", style={"background": bg}),
        html.Div([
            html.Div(value, className="kpi-val", style={"color": color}),
            html.Div(label, className="kpi-label"),
            html.Div(sub,   className="kpi-sub"),
        ]),
    ], className="kpi-card")


def card(title, question, *children):
    return html.Div([
        html.Div([
            html.Span(title),
            html.Span(question, className="ns-card-q"),
        ], className="ns-card-title"),
        *children,
    ], className="ns-card")


# ── Figures initiales ────────────────────────────────────────────────
FIG_VOL  = fig_volume()
FIG_F1   = fig_f1()
FIG_CONF = fig_confusion()
FIG_FEAT = fig_features()

# ── KPI worst class ──────────────────────────────────────────────────
worst_label = WORST["label"] if WORST is not None else "—"
worst_f1    = f"{WORST['f1']:.2f}%" if WORST is not None else "—"

# ═══════════════════════════════════════════════════════════════════════
# LAYOUT
# ═══════════════════════════════════════════════════════════════════════
app.layout = html.Div([
    dcc.Interval(id="tick", interval=30_000, n_intervals=0),
    html.Div(className="scanline"),

    # HEADER
    html.Div([
        html.Div([
            html.Div("NS", className="ns-logo-icon"),
            html.Div([html.Span("Net"), html.Span("Sentinel")],
                     className="ns-logo-text"),
        ], className="ns-logo"),
        html.Div([
            html.Div([html.Div(className="badge-live-dot"), "LIVE"],
                     className="badge-live"),
            html.Div(id="ns-clock", className="ns-time"),
        ], className="ns-right"),
    ], className="ns-header"),

    # KPI — 4 chiffres clés
    html.Div([
        kpi("◈", "Flows Analysés",  f"{TOTAL_FLOWS:,}",
            f"{len(label_counts)} classes",
            "rgba(0,212,255,0.07)", A),
        kpi("◈", "Attaques Détectées", f"{TOTAL_ATTACKS:,}",
            f"{len(ATTACK_TYPES)} types de menaces",
            "rgba(0,150,199,0.07)", OR),
        kpi("◈", "Accuracy Modèle",  f"{ACCURACY:.2f}%",
            f"F1 : {METRICS_DICT.get('F1-Score', 0):.2f}%",
            "rgba(0,212,255,0.07)", A),
        kpi("◈", "Classe la + faible", worst_label,
            f"F1 = {worst_f1}",
            "rgba(224,82,99,0.07)", R),
    ], className="kpi-row"),

    # FILTRE
    html.Div([
        html.Span("Filtre menace", className="filter-label"),
        dcc.Dropdown(
            id="sel",
            options=[{"label": "Toutes les menaces", "value": "ALL"}] +
                    [{"label": t, "value": t} for t in sorted(ATTACK_TYPES)],
            value="ALL", clearable=False,
            style={"width":"260px","fontSize":"11px"},
        ),
        html.Span(id="sel-badge",
                  style={"fontSize":"10px","color":A,
                         "fontFamily":"JetBrains Mono,monospace"}),
    ], className="filter-bar"),

    # GRAPHIQUES — 2 × 2
    html.Div([
        card("Volume par type d'attaque", "Qu'est-ce qui attaque ?",
             dcc.Graph(id="g-vol",  figure=FIG_VOL,
                       config={"displayModeBar": False})),
        card("F1-Score par classe", "Détecte-t-on bien chaque menace ?",
             dcc.Graph(id="g-f1",   figure=FIG_F1,
                       config={"displayModeBar": False})),
    ], className="grid-2"),

    html.Div([
        card("Matrice de confusion", "Qu'est-ce qu'on rate ou confond ?",
             dcc.Graph(id="g-conf", figure=FIG_CONF,
                       config={"displayModeBar": False})),
        card("Feature Importance", "Sur quels signaux repose la détection ?",
             dcc.Graph(id="g-feat", figure=FIG_FEAT,
                       config={"displayModeBar": False})),
    ], className="grid-2"),

    # TABLE — pleine largeur
    html.Div([
        html.Div([
            html.Div("Rapport détaillé par classe",
                     className="ns-card-title",
                     style={"display":"flex","alignItems":"center","gap":"8px",
                            "marginBottom":"14px"}),
            html.Div(id="tbl"),
        ], className="ns-card"),
    ], className="grid-full"),

], style={"backgroundColor": BG, "minHeight": "100vh"})


# ═══════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════

@app.callback(Output("ns-clock", "children"), Input("tick", "n_intervals"))
def clock(_):
    return datetime.now().strftime("%Y-%m-%d  //  %H:%M:%S")


@app.callback(
    Output("g-vol",    "figure"),
    Output("g-f1",     "figure"),
    Output("sel-badge","children"),
    Output("tbl",      "children"),
    Input("sel",       "value"),
)
def apply_filter(sel):
    badge = f"● {sel}" if sel and sel != "ALL" else ""
    return fig_volume(sel), fig_f1(sel), badge, make_table(sel)



if __name__ == "__main__":
    app.run(debug=True)
