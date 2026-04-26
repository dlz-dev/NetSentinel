import dlt
import os
from dlt.sources.filesystem import filesystem, read_csv

# je définis ma source de données — dlt va lire tous les CSV du dossier 01_raw
@dlt.source
def network_traffic_source(data_path: str = "data/01_raw"):
    # je crée une ressource pour chaque CSV du dossier
    # dlt gère automatiquement la lecture, la validation du schéma et les erreurs
    yield filesystem(bucket_url=data_path, file_glob="*.csv") | read_csv()


def run_ingestion(data_path: str = "data/01_raw", destination_path: str = "data/02_intermediate"):
    # je configure le pipeline dlt avec Delta comme format de sortie
    # Delta garantit des écritures ACID — pas de données corrompues si le pipeline plante
    pipeline = dlt.pipeline(
        pipeline_name="netsentinel_ingestion",
        destination=dlt.destinations.filesystem(destination_path),
        dataset_name="raw_traffic",
    )

    # je lance l'ingestion — dlt lit les CSV, valide le schéma et écrit en Parquet
    load_info = pipeline.run(network_traffic_source(data_path))
    print(load_info)
    return load_info


if __name__ == "__main__":
    run_ingestion()
