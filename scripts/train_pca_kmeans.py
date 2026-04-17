import json
import pickle
import sys
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
FEATURES_PATH = DATA_DIR / "user_features.parquet"
CLUSTERS_PATH = MODELS_DIR / "user_clusters.parquet"
VOCAB_PATH = MODELS_DIR / "vocab.pkl"

N_PCA = 50
N_CLUSTERS = 50
EVAL_SAMPLE = 500


def mean_average_precision(
    vectors_norm: np.ndarray,
    labels: np.ndarray,
    sample_size: int = EVAL_SAMPLE,
    top_k: int = 10,
) -> float:
    # MAP@K: релевантность определяется принадлежностью к одному кластеру.
    rng = np.random.default_rng(42)
    idx = rng.choice(
        len(vectors_norm), size=min(sample_size, len(vectors_norm)), replace=False
    )
    aps = []
    for i in idx:
        sims = vectors_norm @ vectors_norm[i]
        sims[i] = -2.0
        top_k_idx = np.argsort(sims)[::-1][:top_k]
        same_cluster = (labels[top_k_idx] == labels[i]).astype(float)
        precisions = np.cumsum(same_cluster) / (np.arange(top_k) + 1)
        ap = float(np.sum(precisions * same_cluster) / max(same_cluster.sum(), 1))
        aps.append(ap)
    return float(np.mean(aps))


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not FEATURES_PATH.exists():
        print(
            f"ERROR: {FEATURES_PATH} не найден",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.read_parquet(FEATURES_PATH)
    X = df.values.astype(np.float32)

    mlflow.set_tracking_uri("http://mlflow:5000")
    mlflow.set_experiment("pca_kmeans_training")

    with mlflow.start_run():
        scaler = StandardScaler()
        X_sc = scaler.fit_transform(X)

        # n_components не может превышать число признаков или число пользователей
        n_pca = min(N_PCA, X.shape[1], X.shape[0])
        pca = PCA(n_components=n_pca, random_state=42)
        X_pca = pca.fit_transform(X_sc).astype(np.float32)

        kmeans = MiniBatchKMeans(
            n_clusters=N_CLUSTERS,
            random_state=42,
            batch_size=4096,
            n_init=5,
            max_iter=100,
        )
        labels = kmeans.fit_predict(X_pca)

        # MAP@10 — оценка качества кластеризации
        norms = np.linalg.norm(X_pca, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        map_score = mean_average_precision(X_pca / norms, labels)

        sample_size = min(10_000, len(X_pca))
        rng = np.random.default_rng(42)
        idx = rng.choice(len(X_pca), size=sample_size, replace=False)
        sil_score = float(
            silhouette_score(X_pca[idx], labels[idx], sample_size=sample_size)
        )

        mlflow.log_params(
            {
                "n_components": n_pca,
                "n_clusters": N_CLUSTERS,
                "random_state": 42,
                "n_users": len(df),
                "n_features": X.shape[1],
            }
        )
        mlflow.log_metrics(
            {
                "inertia": float(kmeans.inertia_),
                "silhouette_score": sil_score,
                "map_at_10": map_score,
                "explained_variance_ratio": float(pca.explained_variance_ratio_.sum()),
            }
        )

        # top_cats — топ-3 категории на пользователя для объяснений мэтчей
        cat_cols = [c for c in df.columns if c.startswith("cat_")]
        if cat_cols:
            top_cats_list = []
            for _, row in df[cat_cols].iterrows():
                significant = row[row > 0.05].nlargest(3)
                names = [c.removeprefix("cat_") for c in significant.index.tolist()]
                top_cats_list.append(json.dumps(names, ensure_ascii=False))
        else:
            top_cats_list = ["[]"] * len(df)

        with open(MODELS_DIR / "scaler.pkl", "wb") as fh:
            pickle.dump(scaler, fh)
        with open(MODELS_DIR / "pca.pkl", "wb") as fh:
            pickle.dump(pca, fh)
        with open(MODELS_DIR / "kmeans.pkl", "wb") as fh:
            pickle.dump(kmeans, fh)

        mlflow.log_artifact(str(MODELS_DIR / "scaler.pkl"))
        mlflow.log_artifact(str(MODELS_DIR / "pca.pkl"))
        mlflow.log_artifact(str(MODELS_DIR / "kmeans.pkl"))

        # Обновляем vocab с актуальным feature_columns
        if VOCAB_PATH.exists():
            with open(VOCAB_PATH, "rb") as fh:
                vocab = pickle.load(fh)
            vocab["feature_columns"] = df.columns.tolist()
            with open(VOCAB_PATH, "wb") as fh:
                pickle.dump(vocab, fh)

        pca_cols = [f"pca_{i}" for i in range(pca.n_components_)]
        df_out = pd.DataFrame(X_pca, columns=pca_cols, index=df.index)
        df_out["cluster"] = labels
        df_out["top_cats"] = top_cats_list
        df_out.to_parquet(CLUSTERS_PATH)

    print(
        f"MAP@10={map_score:.4f} | silhouette={sil_score:.4f} | "
        f"inertia={kmeans.inertia_:.0f} | users={len(df):,} | "
        f"features={X.shape[1]} | pca={n_pca} | clusters={N_CLUSTERS}"
    )
    print(
        f"Saved: {MODELS_DIR / 'scaler.pkl'}, pca.pkl, kmeans.pkl, user_clusters.parquet"
    )
    print("Next: python scripts/seed_clusters.py")


if __name__ == "__main__":
    main()
