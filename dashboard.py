import pandas as pd
import dash
from dash import dcc, html, Input, Output
import plotly.express as px
import plotly.graph_objects as go
import numpy as np

feature_importance = pd.read_parquet("data/dashboard/feature_importance")
label_counts = pd.read_parquet("data/dashboard/label_counts")
metrics = pd.read_parquet("data/dashboard/metrics")
detection_rates = pd.read_parquet("data/dashboard/detection_rates")
conf_matrix = pd.read_parquet("data/dashboard/conf_matrix")
per_class = pd.read_parquet("data/dashboard/per_class_metrics")

LABELS = ["Benign", "DDoS_LOIT", "DoS_Hulk", "Port_Scan",
          "FTP-Patator", "DoS_GoldenEye", "DoS_Slowhttptest",
          "SSH-Patator", "Botnet_ARES", "DoS_Slowloris"]

GRAPH_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", family="DM Sans, sans-serif"),
    margin=dict(l=20, r=20, t=30, b=20),
)

ATTACK_TYPES = [l for l in label_counts["label"].tolist() if l != "Benign"]

matrix = np.zeros((10, 10), dtype=int)
for _, row in conf_matrix.iterrows():
    i = int(row["label_index"])
    j = int(row["final_prediction"])
    matrix[i][j] = int(row["count"])

app = dash.Dash(__name__)

app.index_string = '''
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>NetSentinel SOC</title>
    {%favicon%}
    {%css%}
    <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        html, body, #react-entry-point, ._dash-loading, .dash-renderer {
            height: 100%;
            width: 100%;
        }

        body {
            background: #0f1117;
            font-family: "DM Sans", sans-serif;
            color: #e2e8f0;
        }

        .wrapper {
            display: flex;
            min-height: 100vh;
            width: 100%;
        }

        /* Sidebar */
        .sidebar {
            width: 220px;
            min-height: 100vh;
            background: #1a1d27;
            border-right: 1px solid #2a2d3e;
            display: flex;
            flex-direction: column;
            position: fixed;
            top: 0; left: 0;
            z-index: 100;
        }
        .sidebar-logo {
            padding: 20px;
            border-bottom: 1px solid #2a2d3e;
            font-size: 16px;
            font-weight: 600;
            text-decoration: none;
            color: #e2e8f0;
            display: block;
        }
        .sidebar-logo span { color: #6c63ff; }
        .sidebar-logo-sub {
            font-size: 10px;
            color: #8892a4;
            font-family: "DM Mono", monospace;
            margin-top: 3px;
        }
        .sidebar-section {
            padding: 16px 12px 8px;
            font-size: 10px;
            color: #8892a4;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            font-family: "DM Mono", monospace;
        }
        .sidebar-item {
            padding: 10px 20px;
            font-size: 13px;
            color: #8892a4;
            cursor: pointer;
            border-radius: 6px;
            margin: 2px 8px;
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
            transition: all 0.15s;
        }
        .sidebar-item:hover { background: #2a2d3e; color: #e2e8f0; }
        .sidebar-item.active {
            background: rgba(108,99,255,0.15);
            color: #6c63ff;
            border-left: 3px solid #6c63ff;
        }
        .sidebar-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
        .sidebar-footer {
            margin-top: auto;
            padding: 16px 20px;
            border-top: 1px solid #2a2d3e;
            font-size: 10px;
            color: #2a2d3e;
            font-family: "DM Mono", monospace;
            line-height: 1.6;
        }

        /* Main */
        .main {
            margin-left: 220px;
            flex: 1;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* Header */
        .header {
            padding: 16px 28px;
            border-bottom: 1px solid #2a2d3e;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1a1d27;
            position: sticky;
            top: 0;
            z-index: 99;
        }
        .header-title { font-size: 18px; font-weight: 600; }
        .header-sub { font-size: 11px; color: #8892a4; margin-top: 2px; font-family: "DM Mono", monospace; }
        .status-badge {
            background: rgba(81,207,102,0.1);
            border: 1px solid rgba(81,207,102,0.25);
            color: #51cf66;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 11px;
            font-family: "DM Mono", monospace;
        }

        /* Filters */
        .filters-bar {
            display: flex;
            gap: 16px;
            align-items: center;
            padding: 14px 28px;
            background: #1a1d27;
            border-bottom: 1px solid #2a2d3e;
            flex-wrap: wrap;
        }
        .filter-group {
            display: flex;
            align-items: center;
            gap: 10px;
            flex: 1;
            min-width: 160px;
        }
        .filter-label {
            font-size: 10px;
            color: #8892a4;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-family: "DM Mono", monospace;
            white-space: nowrap;
        }
        .search-input {
            background: #0f1117;
            border: 1px solid #2a2d3e;
            border-radius: 6px;
            color: #e2e8f0;
            padding: 8px 12px;
            font-family: "DM Mono", monospace;
            font-size: 12px;
            width: 100%;
            outline: none;
            transition: border-color 0.15s;
        }
        .search-input:focus { border-color: #6c63ff; }

        /* Content */
        .content { padding: 24px 28px; flex: 1; }

        /* KPIs */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 14px;
            margin-bottom: 20px;
        }
        .kpi-card {
            background: #1a1d27;
            border: 1px solid #2a2d3e;
            border-radius: 8px;
            padding: 18px;
            position: relative;
            overflow: hidden;
        }
        .kpi-card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
        }
        .kpi-card.blue::before { background: #6c63ff; }
        .kpi-card.red::before { background: #ff6b6b; }
        .kpi-card.green::before { background: #51cf66; }
        .kpi-card.amber::before { background: #fcc419; }
        .kpi-label { font-size: 10px; color: #8892a4; text-transform: uppercase; letter-spacing: 1px; font-family: "DM Mono", monospace; margin-bottom: 8px; }
        .kpi-value { font-size: 30px; font-weight: 600; line-height: 1; }
        .kpi-value.blue { color: #6c63ff; }
        .kpi-value.red { color: #ff6b6b; }
        .kpi-value.green { color: #51cf66; }
        .kpi-value.amber { color: #fcc419; }
        .kpi-sub { font-size: 10px; color: #8892a4; margin-top: 6px; font-family: "DM Mono", monospace; }

        /* Charts */
        .charts-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
        .charts-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; margin-bottom: 14px; }
        .charts-grid-full { margin-bottom: 14px; }

        .chart-card {
            background: #1a1d27;
            border: 1px solid #2a2d3e;
            border-radius: 8px;
            padding: 18px;
        }
        .chart-title {
            font-size: 10px;
            color: #8892a4;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            font-family: "DM Mono", monospace;
            margin-bottom: 14px;
            padding-bottom: 12px;
            border-bottom: 1px solid #2a2d3e;
        }

        /* Section anchor offset for sticky header */
        .section-anchor {
            scroll-margin-top: 120px;
        }

        /* Table */
        .alert-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .alert-table th {
            text-align: left;
            padding: 8px 12px;
            font-size: 10px;
            color: #8892a4;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-family: "DM Mono", monospace;
            border-bottom: 1px solid #2a2d3e;
        }
        .alert-table td {
            padding: 10px 12px;
            border-bottom: 1px solid #1e2130;
            font-family: "DM Mono", monospace;
            font-size: 11px;
        }
        .alert-table tr:hover td { background: #2a2d3e; }
        .badge {
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-family: "DM Mono", monospace;
            display: inline-block;
        }
        .badge.good { background: rgba(81,207,102,0.15); color: #51cf66; border: 1px solid rgba(81,207,102,0.3); }
        .badge.medium { background: rgba(252,196,25,0.15); color: #fcc419; border: 1px solid rgba(252,196,25,0.3); }
        .badge.bad { background: rgba(255,107,107,0.15); color: #ff6b6b; border: 1px solid rgba(255,107,107,0.3); }

        /* Dropdown overrides */
        .Select-control { background-color: #0f1117 !important; border-color: #2a2d3e !important; }
        .Select-menu-outer { background-color: #1a1d27 !important; border-color: #2a2d3e !important; }
        .Select-option { background-color: #1a1d27 !important; color: #e2e8f0 !important; }
        .Select-option:hover, .Select-option.is-focused { background-color: #2a2d3e !important; }
        .Select-value-label { color: #e2e8f0 !important; }
        .Select-placeholder { color: #8892a4 !important; }

        html { scroll-behavior: smooth; }
    </style>
</head>
<body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
'''

app.layout = html.Div([
    html.Div([

        # Sidebar
        html.Div([
            html.A([
                html.Div([html.Span("Net"), html.Span("Sentinel", style={"color": "#6c63ff"})]),
                html.Div("IDS — SOC Platform", className="sidebar-logo-sub"),
            ], href="#section-top", className="sidebar-logo"),

            html.Div("Vue d'ensemble", className="sidebar-section"),
            html.A([html.Div(className="sidebar-dot"), "Dashboard"], href="#section-top", className="sidebar-item active"),
            html.A([html.Div(className="sidebar-dot"), "Alertes"], href="#section-par-classe", className="sidebar-item"),

            html.Div("Analyse", className="sidebar-section"),
            html.A([html.Div(className="sidebar-dot"), "Features"], href="#section-features", className="sidebar-item"),
            html.A([html.Div(className="sidebar-dot"), "Performance"], href="#section-performance", className="sidebar-item"),
            html.A([html.Div(className="sidebar-dot"), "Par classe"], href="#section-par-classe", className="sidebar-item"),

            html.Div("Modèle", className="sidebar-section"),
            html.A([html.Div(className="sidebar-dot"), "Random Forest"], href="#section-performance", className="sidebar-item"),
            html.A([html.Div(className="sidebar-dot"), "Ensemble x10"], href="#section-performance", className="sidebar-item"),

            html.Div([
                "NetSentinel v1.0",
                html.Br(),
                "HELMo IA Bloc 2 — 2026"
            ], className="sidebar-footer"),
        ], className="sidebar"),

        # Main
        html.Div([

            # Header
            html.Div([
                html.Div([
                    html.Div("SOC Dashboard", className="header-title"),
                    html.Div("BCCC-CIC-IDS-2017 — Ensemble Random Forest — 500 arbres", className="header-sub"),
                ]),
                html.Div("● Système actif", className="status-badge"),
            ], className="header"),

            # Filtres
            html.Div([
                html.Div([
                    html.Div("Recherche feature", className="filter-label"),
                    dcc.Input(id="search-feature", type="text",
                              placeholder="ex: bwd_init_win_bytes...",
                              className="search-input", debounce=True),
                ], className="filter-group"),

                html.Div([
                    html.Div("Type d'attaque", className="filter-label"),
                    dcc.Dropdown(
                        id="filter-attack",
                        options=[{"label": a, "value": a} for a in ATTACK_TYPES],
                        placeholder="Toutes les attaques",
                        multi=True,
                        style={"width": "100%", "fontFamily": "DM Mono, monospace", "fontSize": "12px"},
                    ),
                ], className="filter-group", style={"flex": "2"}),

                html.Div([
                    html.Div(id="slider-label", className="filter-label"),
                    dcc.Slider(id="filter-detection", min=0, max=100, step=5, value=0,
                               marks={0: "0%", 50: "50%", 80: "80%", 100: "100%"},
                               tooltip={"placement": "bottom"}),
                ], className="filter-group", style={"flex": "2"}),
            ], className="filters-bar"),

            # Content
            html.Div([

                # KPIs
                html.Div([
                    html.Div([
                        html.Div("Connexions analysées", className="kpi-label"),
                        html.Div("337K", className="kpi-value blue"),
                        html.Div("dataset complet", className="kpi-sub"),
                    ], className="kpi-card blue"),
                    html.Div([
                        html.Div("Attaques détectées", className="kpi-label"),
                        html.Div("287K", className="kpi-value red"),
                        html.Div("sur 337K connexions", className="kpi-sub"),
                    ], className="kpi-card red"),
                    html.Div([
                        html.Div("F1-Score global", className="kpi-label"),
                        html.Div("98.81%", className="kpi-value green"),
                        html.Div("ensemble 500 arbres", className="kpi-sub"),
                    ], className="kpi-card green"),
                    html.Div([
                        html.Div("Faux négatifs", className="kpi-label"),
                        html.Div("~1.2%", className="kpi-value amber"),
                        html.Div("attaques manquées", className="kpi-sub"),
                    ], className="kpi-card amber"),
                ], className="kpi-grid", id="section-top"),

                # Section features
                html.Div([
                    html.Div([
                        html.Div("Top features — Importance MDI", className="chart-title"),
                        dcc.Graph(id="graph-features", config={"displayModeBar": False}),
                    ], className="chart-card"),
                    html.Div([
                        html.Div("Répartition des attaques", className="chart-title"),
                        dcc.Graph(id="graph-dist", config={"displayModeBar": False}),
                    ], className="chart-card"),
                ], className="charts-grid-2 section-anchor", id="section-features"),

                # Section performance
                html.Div([
                    html.Div([
                        html.Div("Performance globale du modèle", className="chart-title"),
                        dcc.Graph(id="graph-metrics", config={"displayModeBar": False}),
                    ], className="chart-card"),
                    html.Div([
                        html.Div("Taux de détection par type d'attaque", className="chart-title"),
                        dcc.Graph(id="graph-detection", config={"displayModeBar": False}),
                    ], className="chart-card"),
                    html.Div([
                        html.Div("Matrice de confusion", className="chart-title"),
                        dcc.Graph(id="graph-confusion", config={"displayModeBar": False}),
                    ], className="chart-card"),
                ], className="charts-grid-3 section-anchor", id="section-performance"),

                # Section par classe
                html.Div([
                    html.Div([
                        html.Div("Precision / Recall / F1 par classe", className="chart-title"),
                        dcc.Graph(id="graph-per-class", config={"displayModeBar": False}),
                    ], className="chart-card"),
                    html.Div([
                        html.Div("Faux positifs & faux négatifs par classe", className="chart-title"),
                        dcc.Graph(id="graph-fp-fn", config={"displayModeBar": False}),
                    ], className="chart-card"),
                ], className="charts-grid-2 section-anchor", id="section-par-classe"),

                # Tableau récap
                html.Div([
                    html.Div([
                        html.Div("Récapitulatif par classe d'attaque", className="chart-title"),
                        html.Div(id="table-per-class"),
                    ], className="chart-card"),
                ], className="charts-grid-full"),

            ], className="content"),
        ], className="main"),

    ], className="wrapper"),
])


@app.callback(Output("slider-label", "children"), Input("filter-detection", "value"))
def update_slider_label(val):
    return f"Seuil détection min : {val}%"


@app.callback(Output("graph-features", "figure"), Input("search-feature", "value"))
def update_features(search):
    df = feature_importance.copy()
    if search:
        df = df[df["feature"].str.contains(search, case=False, na=False)]
    df = df.head(20)
    fig = px.bar(df, x="importance", y="feature", orientation="h",
                 color="importance",
                 color_continuous_scale=[[0, "#2a2d3e"], [1, "#6c63ff"]])
    fig.update_layout(**GRAPH_LAYOUT, coloraxis_showscale=False, height=450)
    fig.update_traces(marker_line_width=0)
    fig.update_xaxes(gridcolor="#2a2d3e", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)", autorange="reversed")
    return fig


@app.callback(Output("graph-dist", "figure"), Input("filter-attack", "value"))
def update_dist(selected):
    df = label_counts.copy()
    if selected:
        df = df[df["label"].isin(selected)]
    fig = px.bar(df.sort_values("count", ascending=False), x="label", y="count",
                 color="count",
                 color_continuous_scale=[[0, "#2a2d3e"], [1, "#ff6b6b"]])
    fig.update_layout(**GRAPH_LAYOUT, coloraxis_showscale=False, height=450)
    fig.update_traces(marker_line_width=0)
    fig.update_xaxes(gridcolor="rgba(0,0,0,0)", tickangle=30)
    fig.update_yaxes(gridcolor="#2a2d3e")
    return fig


@app.callback(Output("graph-metrics", "figure"), Input("filter-attack", "value"))
def update_metrics(_):
    fig = go.Figure()
    colors = ["#6c63ff", "#6c63ff", "#6c63ff", "#51cf66"]
    for i, (_, row) in enumerate(metrics.iterrows()):
        fig.add_trace(go.Bar(
            x=[row["value"] * 100], y=[row["metric"]], orientation="h",
            marker_color=colors[i], marker_line_width=0,
            text=f"{row['value']*100:.2f}%", textposition="inside",
            textfont=dict(color="#0f1117", size=11), name=row["metric"]
        ))
    fig.update_layout(**GRAPH_LAYOUT, showlegend=False, height=300,
                      xaxis=dict(range=[95, 100], gridcolor="#2a2d3e"))
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    return fig


@app.callback(
    Output("graph-detection", "figure"),
    Input("filter-detection", "value"),
    Input("filter-attack", "value")
)
def update_detection(threshold, selected):
    df = detection_rates.copy()
    df = df[df["taux"] >= threshold]
    if selected:
        df = df[df["label"].isin(selected)]
    fig = px.bar(df.sort_values("taux"), x="taux", y="label", orientation="h",
                 color="taux",
                 color_continuous_scale=[[0, "#ff6b6b"], [0.7, "#fcc419"], [1, "#51cf66"]])
    fig.update_layout(**GRAPH_LAYOUT, coloraxis_showscale=False, height=300)
    fig.update_traces(marker_line_width=0)
    fig.update_xaxes(range=[0, 105], gridcolor="#2a2d3e")
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    return fig


@app.callback(Output("graph-confusion", "figure"), Input("filter-attack", "value"))
def update_confusion(_):
    fig = go.Figure(go.Heatmap(
        z=matrix, x=LABELS, y=LABELS,
        colorscale=[[0, "#1a1d27"], [1, "#6c63ff"]],
        showscale=False,
    ))
    fig.update_layout(**GRAPH_LAYOUT, height=300)
    fig.update_xaxes(tickangle=45, tickfont=dict(size=8))
    fig.update_yaxes(tickfont=dict(size=8))
    return fig


@app.callback(
    Output("graph-per-class", "figure"),
    Input("filter-attack", "value"),
    Input("filter-detection", "value")
)
def update_per_class(selected, threshold):
    df = per_class.copy()
    if selected:
        df = df[df["label"].isin(selected + ["Benign"])]
    df = df[df["recall"] >= threshold]
    fig = go.Figure()
    for metric, color in [("precision", "#6c63ff"), ("recall", "#51cf66"), ("f1", "#fcc419")]:
        fig.add_trace(go.Bar(
            name=metric.capitalize(), x=df["label"], y=df[metric],
            marker_color=color, marker_line_width=0,
        ))
    fig.update_layout(**GRAPH_LAYOUT, barmode="group", height=340,
                      legend=dict(orientation="h", y=1.1),
                      yaxis=dict(range=[60, 101], gridcolor="#2a2d3e"))
    fig.update_xaxes(tickangle=30, gridcolor="rgba(0,0,0,0)")
    return fig


@app.callback(Output("graph-fp-fn", "figure"), Input("filter-attack", "value"))
def update_fp_fn(selected):
    df = per_class.copy()
    if selected:
        df = df[df["label"].isin(selected + ["Benign"])]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Faux positifs", x=df["label"], y=df["fp"],
                         marker_color="#fcc419", marker_line_width=0))
    fig.add_trace(go.Bar(name="Faux négatifs", x=df["label"], y=df["fn"],
                         marker_color="#ff6b6b", marker_line_width=0))
    fig.update_layout(**GRAPH_LAYOUT, barmode="group", height=340,
                      legend=dict(orientation="h", y=1.1),
                      yaxis=dict(gridcolor="#2a2d3e"))
    fig.update_xaxes(tickangle=30, gridcolor="rgba(0,0,0,0)")
    return fig


@app.callback(Output("table-per-class", "children"), Input("filter-attack", "value"))
def update_table(selected):
    df = per_class.copy()
    if selected:
        df = df[df["label"].isin(selected + ["Benign"])]

    def badge(val):
        if val >= 95:
            return html.Span(f"{val}%", className="badge good")
        elif val >= 80:
            return html.Span(f"{val}%", className="badge medium")
        else:
            return html.Span(f"{val}%", className="badge bad")

    rows = [html.Tr([
        html.Td(row["label"]),
        html.Td(badge(row["precision"])),
        html.Td(badge(row["recall"])),
        html.Td(badge(row["f1"])),
        html.Td(row["tp"], style={"color": "#51cf66"}),
        html.Td(row["fp"], style={"color": "#fcc419"}),
        html.Td(row["fn"], style={"color": "#ff6b6b"}),
    ]) for _, row in df.iterrows()]

    return html.Table([
        html.Thead(html.Tr([
            html.Th("Classe"), html.Th("Precision"), html.Th("Recall"), html.Th("F1"),
            html.Th("Vrais positifs"), html.Th("Faux positifs"), html.Th("Faux négatifs"),
        ])),
        html.Tbody(rows),
    ], className="alert-table")


if __name__ == "__main__":
    app.run(debug=True)