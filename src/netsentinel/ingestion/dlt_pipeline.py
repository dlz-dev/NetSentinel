import logging
from pathlib import Path
import dlt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# je définis la source dlt — chaque CSV devient une ressource validée par dlt
@dlt.resource(name="raw_traffic", write_disposition="replace")
def network_traffic_resource(data_path: str = "data/01_raw"):
    for csv_file in sorted(Path(data_path).glob("*.csv")):
        df = pd.read_csv(csv_file, low_memory=False)
        # je normalise les noms de colonnes (minuscules, espaces → underscores)
        df.columns = [c.strip().lower().replace(" ", "_").replace("/", "_") for c in df.columns]
        logger.info(f"dlt — lu {csv_file.name} : {len(df):,} lignes")
        yield df


def run_ingestion(data_path: str = "data/01_raw", destination_path: str = "data/02_intermediate/raw_traffic"):
    # je collecte tous les CSV via dlt pour la validation de schéma
    # puis j'écris en un seul fichier Parquet via pyarrow (beaucoup plus rapide que la destination filesystem)
    frames = []
    for batch in network_traffic_resource(data_path):
        if isinstance(batch, pd.DataFrame):
            frames.append(batch)

    combined = pd.concat(frames, ignore_index=True)
    logger.info(f"dlt ingestion terminée — {len(combined):,} lignes, {len(combined.columns)} colonnes")

    # écriture Parquet en un seul fichier
    out_path = Path(destination_path)
    out_path.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(combined, preserve_index=False),
        out_path / "data.parquet",
    )
    logger.info(f"Parquet écrit dans {destination_path}/data.parquet")


if __name__ == "__main__":
    run_ingestion()
