import pandas as pd
import dash
from dash import dcc, html
import plotly.express as px

# Delta = des fichiers parquet dans un dossier, on lit le dossier
feature_importance = pd.read_parquet("data/dashboard/feature_importance")
label_counts = pd.read_parquet("data/dashboard/label_counts")

app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("NetSentinel — SOC Dashboard"),

    html.H2("Top 20 features les plus importantes"),
    dcc.Graph(figure=px.bar(
        feature_importance.head(20),
        x="importance",
        y="feature",
        orientation="h",
        labels={"importance": "Importance (MDI)", "feature": "Feature"}
    ).update_layout(yaxis={"autorange": "reversed"})),

    html.H2("Répartition des attaques"),
    dcc.Graph(figure=px.bar(
        label_counts,
        x="label",
        y="count",
        labels={"label": "Type d'attaque", "count": "Nombre de connexions"}
    )),
])

if __name__ == "__main__":
    app.run(debug=True)