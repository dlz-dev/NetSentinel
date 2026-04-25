from kedro.pipeline import Node, Pipeline

from .nodes import prepare_features, train_cross_validator, train_ensemble


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline([
        Node(
            func=prepare_features,
            inputs=["clean_traffic", "params:data_processing"],
            outputs=["train_set", "test_set"],
            name="prepare_features_node",
        ),
        Node(
            func=train_cross_validator,
            inputs=["train_set", "params:data_science"],
            outputs="cv_model",
            name="train_cross_validator_node",
        ),
        Node(
            func=train_ensemble,
            inputs=["train_set", "params:data_science"],
            outputs="ensemble_models",
            name="train_ensemble_node",
        ),
    ])
