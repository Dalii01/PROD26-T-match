# Запуск:
#   python -m app.model.evaluation.evaluate_map
#   python -m app.model.evaluation.evaluate_map --k 10 --sample 500

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

_BASE_DIR = Path(__file__).parent.parent.parent.parent
CLUSTERS_PATH = _BASE_DIR / "models" / "user_clusters.parquet"


def average_precision_at_k(
    ranked_clusters: np.ndarray, query_cluster: int, k: int
) -> float:
    # Считаем AP@K для одного пользователя.
    # Проходим по топ-K рекомендациям и смотрим, на каких позициях встречается нужный кластер.
    # Чем выше позиция — тем больший вклад в итоговый score.
    hits = 0
    score = 0.0
    for i, c in enumerate(ranked_clusters[:k], start=1):
        if c == query_cluster:
            hits += 1
            score += hits / i
    if hits == 0:
        return 0.0
    return score / hits


def evaluate(k: int = 100, sample: int = 200, seed: int = 42) -> dict:
    if not CLUSTERS_PATH.exists():
        raise FileNotFoundError(f"Файл не найден: {CLUSTERS_PATH}")

    # Загружаем PCA-векторы и кластеры из parquet-файла
    df = pd.read_parquet(CLUSTERS_PATH)
    pca_cols = [c for c in df.columns if c.startswith("pca_")]

    if "cluster" not in df.columns:
        raise ValueError("Колонка 'cluster' не найдена в parquet-файле.")
    if not pca_cols:
        raise ValueError("PCA-колонки не найдены в parquet-файле.")

    raw = df[pca_cols].values.astype(np.float32)
    clusters = df["cluster"].values.astype(np.int32)
    n = len(df)

    # Нормализуем векторы — тогда скалярное произведение равно cosine similarity
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = raw / norms

    # Случайно выбираем пользователей для оценки — не нужно гонять по всем 15к
    rng = np.random.default_rng(seed)
    query_idx = rng.choice(n, size=min(sample, n), replace=False)

    ap_scores = []
    cosine_scores = []
    covered = 0

    for idx in query_idx:
        q_vec = vecs[idx]
        q_cluster = clusters[idx]

        # Считаем cosine со всеми пользователями, себя исключаем
        sims = vecs @ q_vec
        sims[idx] = -2.0

        # Берём топ-K по cosine — это наш ranked list
        top_idx = np.argsort(sims)[::-1][:k]
        top_clusters = clusters[top_idx]
        top_sims = sims[top_idx]

        # Если в кластере только этот пользователь — пропускаем, метрика была бы 0
        relevant_total = int((clusters == q_cluster).sum()) - 1
        if relevant_total == 0:
            continue

        ap = average_precision_at_k(top_clusters, q_cluster, k)
        ap_scores.append(ap)
        cosine_scores.append(float(top_sims.mean()))

        # Coverage: хотя бы один из топ-K попал в правильный кластер
        if any(c == q_cluster for c in top_clusters):
            covered += 1

    map_k = float(np.mean(ap_scores)) if ap_scores else 0.0
    mean_cosine = float(np.mean(cosine_scores)) if cosine_scores else 0.0
    coverage = covered / len(query_idx) if query_idx.size > 0 else 0.0

    return {
        "map_at_k": round(map_k, 4),
        "mean_cosine_at_k": round(mean_cosine, 4),
        "coverage": round(coverage, 4),
        "k": k,
        "users_evaluated": len(ap_scores),
        "total_users": n,
        "n_clusters": int(np.unique(clusters).size),
    }


def _print_results(results: dict) -> None:
    k = results["k"]
    print(f"\n{'=' * 40}")
    print(f"  Результаты оценки модели (MAP@{k})")
    print(f"{'=' * 40}")
    print(f"  Всего пользователей:      {results['total_users']}")
    print(f"  Оценено пользователей:    {results['users_evaluated']}")
    print(f"  Кластеров:                {results['n_clusters']}")
    print(f"{'-' * 40}")
    print(f"  MAP@{k}:                   {results['map_at_k']:.4f}")
    print(f"  Среднее cosine@{k}:        {results['mean_cosine_at_k']:.4f}")
    print(f"  Coverage@{k}:             {results['coverage']:.2%}")
    print(f"{'=' * 40}")
    print("\nОграничения:")
    print("  - Кластер как таргет — косвенная метрика, не реальные мэтчи.")
    print("  - Прямого ground truth (реальных мэтчей пользователей) нет.")
    print("  - При малом кластере метрика занижена.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate MLRecommender MAP@K")
    parser.add_argument(
        "--k", type=int, default=100, help="Глубина ранжирования (default: 100)"
    )
    parser.add_argument(
        "--sample", type=int, default=200, help="Кол-во запросов (default: 200)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)"
    )
    args = parser.parse_args()

    results = evaluate(k=args.k, sample=args.sample, seed=args.seed)
    _print_results(results)
