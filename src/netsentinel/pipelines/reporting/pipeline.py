from kedro.pipeline import Node, Pipeline

from .nodes import evaluate_ensemble, export_dashboard


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline([
        Node(
            func=evaluate_ensemble,
            inputs=["ensemble_models", "cv_model", "test_set"],
            outputs="metrics",
            name="evaluate_ensemble_node",
        ),
        Node(
            func=export_dashboard,
            inputs=["test_set", "cv_model"],
            outputs="dashboard_export",
            name="export_dashboard_node",
        ),
    ])
