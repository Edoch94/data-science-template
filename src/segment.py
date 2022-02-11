from typing import Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from hydra.utils import to_absolute_path
from omegaconf import DictConfig
from prefect import Flow, task
from prefect.engine.results import LocalResult
from prefect.engine.serializers import PandasSerializer
from sklearn.cluster import AgglomerativeClustering, KMeans, SpectralClustering
from sklearn.decomposition import PCA
from yellowbrick.cluster import KElbowVisualizer

import wandb
from helper import log_data, artifact_task
import bentoml
import bentoml.sklearn

OUTPUT_DIR = "data/final/"
OUTPUT_FILE = "{task_name}.csv"
TASK_OUTPUT = LocalResult(
    OUTPUT_DIR,
    location=OUTPUT_FILE,
    serializer=PandasSerializer("csv", serialize_kwargs={"index": False}),
)


@artifact_task(result=TASK_OUTPUT)
def reduce_dimension(
    df: pd.DataFrame, n_components: int, columns: list
) -> pd.DataFrame:
    pca = PCA(n_components=n_components)
    return pd.DataFrame(pca.fit_transform(df), columns=columns)


@task
def get_3d_projection(pca_df: pd.DataFrame) -> dict:
    """A 3D Projection Of Data In The Reduced Dimensionality Space"""
    return {"x": pca_df["col1"], "y": pca_df["col2"], "z": pca_df["col3"]}


@task
def create_3d_plot(projection: dict, image_path: str) -> None:

    # To plot
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(
        projection["x"],
        projection["y"],
        projection["z"],
        cmap="Accent",
        marker="o",
    )
    ax.set_title("A 3D Projection Of Data In The Reduced Dimension")
    plt.savefig(image_path)

    # Log plot
    wandb.log({"pca": wandb.Image(image_path)})


@task
def get_best_k_cluster(
    pca_df: pd.DataFrame, cluster_config, image_path: str
) -> pd.DataFrame:

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111)

    model = eval(cluster_config.algorithm)()
    elbow = KElbowVisualizer(model, metric=cluster_config.metric)

    elbow.fit(pca_df)
    elbow.fig.savefig(image_path)

    k_best = elbow.elbow_value_

    # Log
    wandb.log(
        {
            "elbow": wandb.Image(image_path),
            "k_best": k_best,
            "score_best": elbow.elbow_score_,
        }
    )
    return k_best


@task
def get_clusters_model(
    pca_df: pd.DataFrame, algorithm: str, k: int
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    model = eval(algorithm)(n_clusters=k)

    # Fit model
    return model.fit(pca_df)


@task
def save_model(model, name: str):
    bentoml.sklearn.save(name, model)


@task
def predict(model, pca_df: pd.DataFrame):
    return model.predict(pca_df)


@artifact_task(result=TASK_OUTPUT)
def insert_clusters_to_df(
    df: pd.DataFrame, clusters: np.ndarray
) -> pd.DataFrame:
    return df.assign(clusters=clusters)


@task
def plot_clusters(
    pca_df: pd.DataFrame, preds: np.ndarray, projections: dict, image_path: str
) -> None:
    pca_df["clusters"] = preds

    plt.figure(figsize=(10, 8))
    ax = plt.subplot(111, projection="3d")
    ax.scatter(
        projections["x"],
        projections["y"],
        projections["z"],
        s=40,
        c=pca_df["clusters"],
        marker="o",
        cmap="Accent",
    )
    ax.set_title("The Plot Of The Clusters")

    plt.savefig(image_path)

    # Log plot
    wandb.log({"clusters": wandb.Image(image_path)})


def segment(config: DictConfig) -> None:

    data_config = config.data_catalog
    code_config = config

    with Flow(
        "segmentation",
    ) as flow:

        data = pd.read_csv(
            to_absolute_path(
                f"{data_config.intermediate.dir}/{data_config.intermediate.name}"
            ),
            index_col=0,
        )

        pca_df = reduce_dimension(
            data, code_config.pca.n_components, code_config.pca.columns
        )

        projections = get_3d_projection(pca_df)

        create_3d_plot(projections, to_absolute_path(code_config.image.pca))

        k_best = get_best_k_cluster(
            pca_df,
            code_config.segment,
            to_absolute_path(code_config.image.kmeans),
        )

        model = get_clusters_model(
            pca_df, code_config.segment.algorithm, k_best
        )
        save_model(model, code_config.model_name)

        preds = predict(model, pca_df)

        data = insert_clusters_to_df(data, preds)

        plot_clusters(
            pca_df,
            preds,
            projections,
            to_absolute_path(code_config.image.clusters),
        )

    flow.run()
    flow.register(project_name="customer_segmentation")

    log_data(
        data_config.segmented.name,
        "preprocessed_data",
        to_absolute_path(data_config.segmented.dir),
    )