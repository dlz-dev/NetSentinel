from kedro.pipeline import Pipeline

from netsentinel.pipelines import data_processing, data_science, reporting


def register_pipelines() -> dict[str, Pipeline]:
    data_processing_pipeline = data_processing.create_pipeline()
    data_science_pipeline = data_science.create_pipeline()
    reporting_pipeline = reporting.create_pipeline()

    return {
        # pipeline complet du début à la fin
        "__default__": data_processing_pipeline + data_science_pipeline + reporting_pipeline,
        # pipelines individuels — utiles pour relancer juste une étape
        "data_processing": data_processing_pipeline,
        "data_science": data_science_pipeline,
        "reporting": reporting_pipeline,
    }
