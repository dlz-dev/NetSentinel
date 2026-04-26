import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash
from dash import dcc, html, Input, Output, State
from datetime import datetime
import yaml
from src.netsentinel.agent.threat_analyzer import analyze_threat, generate_soc_report, chat_soc

with open("conf/local/credentials.yml") as f:
    _creds = yaml.safe_load(f)
ANTHROPIC_KEY = _creds["anthropic"]["api_key"]
LANGSMITH_KEY = _creds["langsmith"]["api_key"]

# ═══════════════════════════════════════════════════════════════════════
# PDF HELPERS
# ═══════════════════════════════════════════════════════════════════════
def _strip_md(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    # Drop emojis and any char outside latin-1 (instead of replacing with ?)
    return text.encode('latin-1', errors='ignore').decode('latin-1')


def _make_pdf(md_text: str) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    W = pdf.epw  # effective page width, recalculated after margins

    def mc(h, txt, style="", size=10, color=(194, 204, 224)):
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(*color)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(W, h, txt)

    # En-tête
    mc(14, "NetSentinel - Rapport SOC Executif", "B", 18, (0, 212, 255))
    mc(6, f"Genere le {datetime.now().strftime('%d/%m/%Y a %H:%M')}", "", 9, (104, 117, 149))
    pdf.set_draw_color(0, 212, 255)
    pdf.set_line_width(0.5)
    pdf.ln(3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
    pdf.ln(8)

    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip()

        # Ligne vide
        if not s:
            pdf.ln(3)

        # Séparateur --- → trait horizontal
        elif re.match(r'^-{3,}$', s):
            pdf.ln(2)
            pdf.set_draw_color(104, 117, 149)
            pdf.set_line_width(0.3)
            pdf.set_x(pdf.l_margin)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
            pdf.ln(4)

        # Titre ### (niveau 3)
        elif s.startswith("### "):
            pdf.ln(2)
            mc(7, _strip_md(s[4:]), "B", 11, (255, 165, 0))
            pdf.ln(1)

        # Titre ## (niveau 2)
        elif s.startswith("## "):
            pdf.ln(3)
            mc(8, _strip_md(s[3:]), "B", 13, (0, 212, 255))
            pdf.ln(1)

        # Titre # (niveau 1)
        elif s.startswith("# "):
            pdf.ln(3)
            mc(9, _strip_md(s[2:]), "B", 14, (0, 212, 255))
            pdf.ln(2)

        # Tableau Markdown — collecte toutes les lignes du bloc
        elif s.startswith("|"):
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                # Ignore les lignes séparateurs |---|---|
                if not re.match(r'^\|[\s\-|:]+\|$', row):
                    cells = [c.strip() for c in row.split("|")]
                    cells = [c for c in cells if c]  # retire les vides aux bords
                    if cells:
                        table_rows.append(cells)
                i += 1
            if table_rows:
                col_w = W / max(len(r) for r in table_rows)
                for row_idx, cells in enumerate(table_rows):
                    pdf.set_x(pdf.l_margin)
                    is_header = (row_idx == 0)
                    pdf.set_font("Helvetica", "B" if is_header else "", 9)
                    pdf.set_text_color(*(0, 212, 255) if is_header else (194, 204, 224))
                    for cell in cells:
                        pdf.cell(col_w, 6, _strip_md(cell)[:30], border=0)
                    pdf.ln()
                pdf.ln(2)
            continue  # i déjà avancé dans la boucle interne

        # Ligne **bold** entière
        elif s.startswith("**") and s.endswith("**") and len(s) > 4:
            mc(7, _strip_md(s[2:-2]), "B", 11, (255, 165, 0))

        # Bullet - ou *
        elif s.startswith("- ") or (s.startswith("* ") and not s.startswith("**")):
            mc(6, "  *  " + _strip_md(s[2:]), "", 10, (194, 204, 224))

        # Ligne numérotée 1. 2) etc.
        elif len(s) > 1 and s[0].isdigit() and s[1] in ".)":
            mc(7, _strip_md(s), "B", 10, (255, 165, 0))

        # Texte normal
        else:
            mc(6, _strip_md(s), "", 10, (194, 204, 224))

        i += 1

    return bytes(pdf.output())


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


def fig_f1(sel=None, metric="f1"):
    """Q2 : Détecte-t-on bien chaque type de menace ? — F1 / Precision / Recall"""
    df = per_class.sort_values(metric, ascending=True).copy()
    vals = df[metric]
    colors = [CL if v >= 99 else CML if v >= 97 else CM if v >= 95 else CMH if v >= 93 else CH
              for v in vals]
    opacity = [1.0 if (not sel or sel == "ALL" or l == sel) else 0.12
               for l in df["label"]]
    fig = go.Figure()
    # Barres de fond (full = 100%)
    fig.add_trace(go.Bar(
        x=[100]*len(df), y=df.label, orientation="h",
        marker=dict(color="rgba(255,255,255,0.04)", line=dict(width=0)),
        showlegend=False, hoverinfo="skip",
    ))
    # Barres métrique sélectionnée
    fig.add_trace(go.Bar(
        x=vals, y=df.label, orientation="h",
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
                   range=[max(0, vals.min() - 3), 100.3], ticksuffix="%"),
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


def fig_scatter(sel=None):
    """Q5 : Où se situe chaque attaque sur le plan Precision / Recall ?"""
    df = per_class.copy()
    vol = label_counts.set_index("label")["count"]
    df["volume"] = df["label"].map(vol).fillna(1000)
    sizes = 10 + 50 * (df["volume"] / df["volume"].max())
    colors = [CL if v >= 99 else CML if v >= 97 else CM if v >= 95 else CMH if v >= 93 else CH
              for v in df["f1"]]
    opacity = [1.0 if (not sel or sel == "ALL" or l == sel) else 0.15
               for l in df["label"]]
    fig = go.Figure()
    # Zone verte idéale (precision ≥ 98 et recall ≥ 98)
    fig.add_shape(type="rect", x0=98, x1=100.2, y0=98, y1=100.2,
                  fillcolor="rgba(0,232,126,0.04)", line=dict(width=0))
    fig.add_annotation(x=99.1, y=99.1, text="zone idéale", showarrow=False,
                       font=dict(size=8, color=GR), opacity=0.5)
    fig.add_trace(go.Scatter(
        x=df["recall"], y=df["precision"],
        mode="markers+text",
        marker=dict(size=sizes, color=colors, opacity=opacity,
                    line=dict(width=1, color="rgba(255,255,255,0.15)")),
        text=df["label"], textposition="top center",
        textfont=dict(size=8, color=TX),
        customdata=df[["label","f1","precision","recall","fn","volume"]].values,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "Recall : %{x:.2f}%  ·  Precision : %{y:.2f}%<br>"
            "F1 : %{customdata[1]:.2f}%<br>"
            "Faux négatifs : %{customdata[4]:,}<br>"
            "Volume : %{customdata[5]:,} flows"
            "<extra></extra>"
        ),
    ))
    # Lignes de seuil
    fig.add_hline(y=98, line=dict(color="rgba(255,51,85,0.3)", width=1, dash="dot"))
    fig.add_vline(x=98, line=dict(color="rgba(255,51,85,0.3)", width=1, dash="dot"))
    fig.update_layout(
        **GL, margin=dict(l=40, r=20, t=10, b=40), height=340,
        xaxis=dict(title="Recall (%)", showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                   ticksuffix="%", tickfont=dict(size=9), range=[70, 101]),
        yaxis=dict(title="Precision (%)", showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                   ticksuffix="%", tickfont=dict(size=9), range=[88, 101]),
    )
    return fig


def fig_radar(sel=None):
    """Radar F1 / Precision / Recall pour une classe sélectionnée"""
    if not sel or sel == "ALL":
        row = per_class.mean(numeric_only=True)
        title_label = "Moyenne toutes classes"
    else:
        matches = per_class[per_class["label"] == sel]
        if matches.empty:
            row = per_class.mean(numeric_only=True)
            title_label = "Moyenne"
        else:
            row = matches.iloc[0]
            title_label = sel
    cats = ["F1", "Precision", "Recall", "F1"]
    vals = [row["f1"], row["precision"], row["recall"], row["f1"]]
    fig = go.Figure(go.Scatterpolar(
        r=vals, theta=cats, fill="toself",
        fillcolor="rgba(0,212,255,0.08)",
        line=dict(color=A, width=2),
        hovertemplate="%{theta} : %{r:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        **GL, margin=dict(l=20, r=20, t=30, b=10), height=260,
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=True, range=[max(70, min(vals)-5), 101],
                            tickfont=dict(size=7), gridcolor="rgba(255,255,255,0.06)",
                            ticksuffix="%"),
            angularaxis=dict(tickfont=dict(size=10, color=TX),
                             gridcolor="rgba(255,255,255,0.06)"),
        ),
        annotations=[dict(text=title_label, x=0.5, y=1.08, xref="paper", yref="paper",
                          showarrow=False, font=dict(size=10, color=A))],
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
FIG_VOL     = fig_volume()
FIG_F1      = fig_f1(metric="f1")
FIG_CONF    = fig_confusion()
FIG_FEAT    = fig_features()
FIG_SCATTER = fig_scatter()
FIG_RADAR   = fig_radar()

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
        # sélecteur de métrique pour le graph F1
        html.Span("Métrique", className="filter-label", style={"marginLeft":"24px"}),
        dcc.RadioItems(
            id="metric-sel",
            options=[
                {"label": "F1",        "value": "f1"},
                {"label": "Precision", "value": "precision"},
                {"label": "Recall",    "value": "recall"},
            ],
            value="f1", inline=True,
            style={"fontSize":"11px","color":"#ffffff","gap":"14px","display":"flex"},
            inputStyle={"marginRight":"4px"},
            labelStyle={"color":"#ffffff"},
        ),
    ], className="filter-bar"),

    # BARRE IA — compacte, en haut, avec tous les boutons IA
    dcc.Store(id="chat-open", data=False),
    html.Div([
        html.Div([
            html.Div([
                html.Span("◈ Intelligence IA — Claude"),
                html.Span("Analyse menace · Rapport CISO · Chat libre", className="ns-card-q"),
                # Boutons dans la ligne de titre
                html.Div([
                    html.Button("⚡ Analyser la menace", id="btn-analyze", style={
                        "background": "rgba(0,212,255,0.1)",
                        "border": "1px solid rgba(0,212,255,0.3)",
                        "color": "#00d4ff", "padding": "6px 16px",
                        "borderRadius": "6px", "cursor": "pointer", "fontSize": "11px",
                    }),
                    html.Button("📋 Rapport SOC", id="btn-soc-report", style={
                        "background": "rgba(124,94,247,0.1)",
                        "border": "1px solid rgba(124,94,247,0.3)",
                        "color": "#7c5ef7", "padding": "6px 16px",
                        "borderRadius": "6px", "cursor": "pointer", "fontSize": "11px",
                    }),
                    html.Button("📥 PDF", id="btn-pdf", style={
                        "background": "rgba(255,165,0,0.1)",
                        "border": "1px solid rgba(255,165,0,0.3)",
                        "color": "#ffa500", "padding": "6px 16px",
                        "borderRadius": "6px", "cursor": "pointer", "fontSize": "11px",
                    }),
                    dcc.Download(id="download-pdf"),
                ], style={"display": "flex", "gap": "8px", "marginLeft": "auto"}),
            ], className="ns-card-title", style={"display": "flex", "alignItems": "center", "gap": "12px"}),
            dcc.Loading(
                id="loading-ai",
                type="circle",
                color="#00d4ff",
                children=dcc.Markdown(
                    id="ai-output",
                    style={"color": "#c2cce0", "fontSize": "12px",
                           "lineHeight": "1.9", "fontFamily": "Inter, monospace",
                           "marginTop": "4px"},
                )
            ),
        ], className="ns-card"),
    ], className="grid-full"),

    # GRAPHIQUES — 2 × 2
    html.Div([
        card("Volume par type d'attaque", "Qu'est-ce qui attaque ?",
             dcc.Graph(id="g-vol",  figure=FIG_VOL,
                       config={"displayModeBar": False})),
        card("F1 / Precision / Recall par classe", "Détecte-t-on bien chaque menace ?",
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

    # LIGNE 3 : Scatter P/R + Radar
    html.Div([
        card("Precision vs Recall", "Quelle attaque est la plus difficile à détecter ?",
             dcc.Graph(id="g-scatter", figure=FIG_SCATTER,
                       config={"displayModeBar": False})),
        card("Profil de détection", "Équilibre F1 / Precision / Recall par classe",
             dcc.Graph(id="g-radar", figure=FIG_RADAR,
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

    # BOUTON CHAT FLOTTANT
    html.Button("💬", id="btn-chat-toggle", style={
        "position": "fixed", "bottom": "28px", "right": "28px",
        "zIndex": "1000", "width": "54px", "height": "54px",
        "borderRadius": "50%",
        "background": "linear-gradient(135deg, rgba(0,232,126,0.15), rgba(0,212,255,0.15))",
        "border": "1px solid rgba(0,232,126,0.35)",
        "color": "#00e87e", "fontSize": "22px", "cursor": "pointer",
        "boxShadow": "0 4px 24px rgba(0,232,126,0.18)",
    }),

    # PANNEAU CHAT LATÉRAL (fixe, droite)
    html.Div([
        html.Div([
            html.Span("◈ Chat SOC", style={
                "color": "#00e87e", "fontSize": "13px", "fontWeight": "600",
                "fontFamily": "Inter, monospace",
            }),
            html.Button("✕", id="btn-chat-close", style={
                "background": "none", "border": "none",
                "color": "#687595", "fontSize": "18px", "cursor": "pointer",
            }),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "16px",
                  "borderBottom": "1px solid rgba(0,232,126,0.15)",
                  "paddingBottom": "12px"}),
        # Zone de réponse (scrollable)
        html.Div([
            dcc.Loading(type="circle", color="#00e87e",
                children=dcc.Markdown(id="chat-output", style={
                    "color": "#c2cce0", "fontSize": "12px",
                    "lineHeight": "1.8", "fontFamily": "Inter, monospace",
                })
            ),
        ], style={"flex": "1", "overflowY": "auto", "marginBottom": "14px",
                  "paddingRight": "4px"}),
        # Zone input
        html.Div([
            dcc.Textarea(
                id="chat-input",
                placeholder="Ex : Pourquoi les FTP-Patator sont difficiles à détecter ?",
                style={
                    "width": "100%", "height": "80px", "resize": "none",
                    "background": "rgba(255,255,255,0.04)",
                    "border": "1px solid rgba(255,255,255,0.1)",
                    "borderRadius": "6px", "color": "#c2cce0",
                    "fontSize": "12px", "padding": "9px 12px",
                    "fontFamily": "Inter, monospace", "outline": "none",
                    "boxSizing": "border-box",
                }
            ),
            html.Button("Envoyer →", id="btn-chat", style={
                "marginTop": "8px", "width": "100%",
                "background": "rgba(0,232,126,0.1)",
                "border": "1px solid rgba(0,232,126,0.3)",
                "color": "#00e87e", "padding": "8px",
                "borderRadius": "6px", "cursor": "pointer", "fontSize": "11px",
            }),
        ]),
    ], id="chat-panel", style={
        "display": "none",
        "position": "fixed", "top": "0", "right": "0",
        "height": "100vh", "width": "380px",
        "background": "#0c1525",
        "borderLeft": "1px solid rgba(0,232,126,0.12)",
        "zIndex": "999", "padding": "24px 18px",
        "flexDirection": "column",
        "boxShadow": "-8px 0 40px rgba(0,0,0,0.5)",
    }),

], style={"backgroundColor": BG, "minHeight": "100vh"})


# ═══════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════

@app.callback(Output("ns-clock", "children"), Input("tick", "n_intervals"))
def clock(_):
    return datetime.now().strftime("%Y-%m-%d  //  %H:%M:%S")


@app.callback(
    Output("sel", "value"),
    Input("g-vol", "clickData"),
    Input("g-scatter", "clickData"),
    prevent_initial_call=True,
)
def click_select(click_vol, click_scatter):
    """Clic sur une barre du volume ou une bulle du scatter → met à jour le filtre"""
    from dash import ctx
    triggered = ctx.triggered_id
    if triggered == "g-vol" and click_vol:
        label = click_vol["points"][0]["customdata"][0]
        return label
    if triggered == "g-scatter" and click_scatter:
        label = click_scatter["points"][0]["customdata"][0]
        return label
    return "ALL"


@app.callback(
    Output("g-vol",     "figure"),
    Output("g-f1",      "figure"),
    Output("g-scatter", "figure"),
    Output("g-radar",   "figure"),
    Output("sel-badge", "children"),
    Output("tbl",       "children"),
    Input("sel",        "value"),
    Input("metric-sel", "value"),
)
def apply_filter(sel, metric):
    badge = f"● {sel}" if sel and sel != "ALL" else ""
    return (fig_volume(sel), fig_f1(sel, metric=metric),
            fig_scatter(sel), fig_radar(sel), badge, make_table(sel))



@app.callback(
    Output("ai-output", "children"),
    Input("btn-analyze", "n_clicks"),
    Input("btn-soc-report", "n_clicks"),
    State("sel", "value"),
    prevent_initial_call=True,
)
def ai_action(_n_analyze, _n_report, sel):
    from dash import ctx
    if ctx.triggered_id == "btn-analyze":
        if not sel or sel == "ALL":
            return "Sélectionne un type d'attaque dans le filtre puis clique sur Analyser."
        row = per_class[per_class["label"] == sel]
        if row.empty:
            return "Données non disponibles pour cette classe."
        r = row.iloc[0]
        return analyze_threat(
            attack_type=sel, f1=r["f1"], precision=r["precision"],
            recall=r["recall"], fn=int(r["fn"]),
            anthropic_api_key=ANTHROPIC_KEY, langsmith_api_key=LANGSMITH_KEY,
        )
    if ctx.triggered_id == "btn-soc-report":
        per_class_data = per_class[["label","f1","precision","recall","fn"]].to_dict("records")
        return generate_soc_report(
            metrics_dict=METRICS_DICT, per_class_data=per_class_data,
            total_flows=TOTAL_FLOWS, total_attacks=TOTAL_ATTACKS,
            anthropic_api_key=ANTHROPIC_KEY, langsmith_api_key=LANGSMITH_KEY,
        )
    return dash.no_update


@app.callback(
    Output("download-pdf", "data"),
    Input("btn-pdf", "n_clicks"),
    State("ai-output", "children"),
    prevent_initial_call=True,
)
def download_pdf(n_clicks, report_content):
    if not n_clicks or not report_content:
        return dash.no_update
    return dcc.send_bytes(_make_pdf(report_content), "rapport_soc_netsentinel.pdf")


@app.callback(
    Output("chat-panel", "style"),
    Input("btn-chat-toggle", "n_clicks"),
    Input("btn-chat-close", "n_clicks"),
    State("chat-open", "data"),
    prevent_initial_call=True,
)
def toggle_chat(n_open, n_close, is_open):
    from dash import ctx
    PANEL_BASE = {
        "position": "fixed", "top": "0", "right": "0",
        "height": "100vh", "width": "380px",
        "background": "#0c1525",
        "borderLeft": "1px solid rgba(0,232,126,0.12)",
        "zIndex": "999", "padding": "24px 18px",
        "flexDirection": "column",
        "boxShadow": "-8px 0 40px rgba(0,0,0,0.5)",
    }
    if ctx.triggered_id == "btn-chat-close":
        return {**PANEL_BASE, "display": "none"}
    return {**PANEL_BASE, "display": "flex"}


@app.callback(
    Output("chat-output", "children"),
    Input("btn-chat", "n_clicks"),
    State("chat-input", "value"),
    prevent_initial_call=True,
)
def chat(n_clicks, question):
    if not n_clicks or not question:
        return ""
    context = {
        "accuracy":      METRICS_DICT.get("Accuracy", 0),
        "f1":            METRICS_DICT.get("F1-Score", 0),
        "total_flows":   TOTAL_FLOWS,
        "total_attacks": TOTAL_ATTACKS,
        "attack_types":  ATTACK_TYPES,
    }
    return chat_soc(
        question=question, context=context,
        anthropic_api_key=ANTHROPIC_KEY, langsmith_api_key=LANGSMITH_KEY,
    )


if __name__ == "__main__":
    app.run(debug=True)
