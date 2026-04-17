"""
MLRecommender — look-alike на основе PCA-векторов финансового поведения.

Загружает models/user_clusters.parquet при первом вызове.
Для каждого запроса считает cosine similarity в PCA-пространстве (50D)
и возвращает top-K party_rk ближайших пользователей.

Объяснения мэтчей строятся через SHAP-like атрибуцию:
  вклад каждой исходной фичи = target_approx[j] * candidate_approx[j],
  где approx восстанавливается из PCA-векторов через pca.components_.
  Фичи группируются по смысловому префиксу (cat, mcc, hour/dow, tok).

Холодный старт (party_rk не в обучающей выборке):
  случайная выборка из всего пула.
"""

import json
import pickle
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_BASE_DIR = Path(__file__).parent.parent.parent
CLUSTERS_PATH = _BASE_DIR / "models" / "user_clusters.parquet"
PCA_PATH = _BASE_DIR / "models" / "pca.pkl"
VOCAB_PATH = _BASE_DIR / "models" / "vocab.pkl"

MODEL_VERSION = "1.0.0"

# Шаблоны по доминирующей группе фич.
_TEMPLATES: Dict[str, List[str]] = {
    "cat": [
        "похоже, вы оба любите {details}",
        "вас объединяет {details}",
        "у вас общие интересы: {details}",
    ],
    "temporal": [
        "у вас похожий ритм жизни",
        "вы живёте в схожем темпе",
        "у вас совпадают привычки дня",
    ],
    "mcc": [
        "вы часто бываете в похожих местах",
        "у вас схожие места для покупок",
        "вас объединяют любимые типы заведений",
    ],
    "tok": [
        "у вас общие любимые заведения",
        "вы ходите в похожие места",
        "вас объединяют любимые магазины и кафе",
    ],
    "fallback": [
        "вам может понравиться этот человек",
    ],
}

_SIM_HIGH = 0.75


def _group_contributions(
    feature_columns: List[str],
    contrib: np.ndarray,
) -> Dict[str, float]:
    """Суммирует вклады по смысловым группам фич."""
    groups: Dict[str, float] = {"cat": 0.0, "temporal": 0.0, "mcc": 0.0, "tok": 0.0}
    for name, val in zip(feature_columns, contrib):
        if name.startswith("cat_"):
            groups["cat"] += val
        elif name.startswith(("hour_", "dow_", "weekend")):
            groups["temporal"] += val
        elif name.startswith("mcc_") or name.startswith("merchant"):
            groups["mcc"] += val
        elif name.startswith("tok_"):
            groups["tok"] += val
    return groups


def _top_cat_names(
    feature_columns: List[str],
    contrib: np.ndarray,
    n: int = 2,
) -> List[str]:
    """Возвращает топ-N категорий по вкладу (только cat_* фичи)."""
    cat_pairs = [
        (name[4:], val)  # strip "cat_"
        for name, val in zip(feature_columns, contrib)
        if name.startswith("cat_") and val > 0
    ]
    cat_pairs.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in cat_pairs[:n]]


_GROUP_LABELS: Dict[str, str] = {
    "cat": "общие категории покупок",
    "temporal": "похожий ритм жизни",
    "mcc": "схожие типы заведений",
    "tok": "любимые места и магазины",
}


def _extract_shap_info(
    feature_columns: List[str],
    target_raw: np.ndarray,
    candidate_raw: np.ndarray,
    components: np.ndarray,
) -> Tuple[str, List[str]]:
    """
    Возвращает (dominant_label, joint_top_cats) для LLM-промпта.

    dominant_label — человекочитаемая строка доминирующей группы фич.
    joint_top_cats — категории с наибольшим совместным вкладом обоих пользователей.
    """
    target_feat = target_raw @ components
    candidate_feat = candidate_raw @ components
    contrib = target_feat * candidate_feat

    groups = _group_contributions(feature_columns, contrib)
    best_group = max(groups, key=lambda g: groups[g])
    dominant_label = _GROUP_LABELS.get(best_group, "общие интересы")
    joint_top_cats = _top_cat_names(feature_columns, contrib, n=3)
    return dominant_label, joint_top_cats


def _fmt_list(items: List[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} и {items[-1]}"


def _build_explanation(
    feature_columns: List[str],
    target_raw: np.ndarray,
    candidate_raw: np.ndarray,
    components: np.ndarray,
    score: float,
) -> str:
    """
    SHAP-like атрибуция: восстанавливаем приближённые исходные векторы
    через pca.components_ и смотрим, какая группа фич суммарно вносит
    наибольший положительный вклад в их совместимость.
    """
    # Восстанавливаем приближённые исходные фичи из PCA-координат.
    # target_raw / candidate_raw — ненормализованные PCA-векторы [n_components].
    # components — [n_components × n_features].
    target_feat = target_raw @ components  # [n_features]
    candidate_feat = candidate_raw @ components  # [n_features]

    # Поэлементное произведение = вклад каждой фичи в сходство.
    contrib = target_feat * candidate_feat  # [n_features]

    groups = _group_contributions(feature_columns, contrib)

    # Доминирующая группа с положительным вкладом.
    best_group = max(groups, key=lambda g: groups[g])
    best_val = groups[best_group]

    if best_val <= 0 or score < _SIM_HIGH:
        return random.choice(_TEMPLATES["fallback"])

    if best_group == "cat":
        top_cats = _top_cat_names(feature_columns, contrib, n=2)
        if top_cats:
            details = _fmt_list(top_cats)
            return random.choice(_TEMPLATES["cat"]).format(details=details)

    return random.choice(_TEMPLATES[best_group])


class MLRecommender:
    def __init__(self) -> None:
        self._loaded = False
        self._vectors_norm: Optional[np.ndarray] = None
        self._vectors_raw: Optional[np.ndarray] = None
        self._party_rks: Optional[np.ndarray] = None
        self._rk_to_idx: Dict[str, int] = {}
        # Кластеры для двухэтапного поиска
        self._clusters: Optional[np.ndarray] = None
        self._cluster_to_idx: Dict[int, np.ndarray] = {}
        self._cluster_centroids: Dict[int, np.ndarray] = {}
        # PCA для SHAP-объяснений
        self._pca_components: Optional[np.ndarray] = None
        self._feature_columns: List[str] = []
        # Топ-категории пользователей для LLM-объяснений
        self._rk_to_top_cats: Dict[str, List[str]] = {}

    def _load(self) -> None:
        if self._loaded:
            return

        if not CLUSTERS_PATH.exists():
            self._party_rks = np.array([], dtype=str)
            self._vectors_norm = np.zeros((0, 1), dtype=np.float32)
            self._vectors_raw = np.zeros((0, 1), dtype=np.float32)
            self._loaded = True
            return

        df = pd.read_parquet(CLUSTERS_PATH)
        pca_cols = [c for c in df.columns if c.startswith("pca_")]
        raw = df[pca_cols].values.astype(np.float32)

        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._vectors_norm = raw / norms
        self._vectors_raw = raw

        self._party_rks = df.index.values.astype(str)
        self._rk_to_idx = {rk: i for i, rk in enumerate(self._party_rks)}

        # Кластерный индекс: cluster_id → массив позиций в матрице.
        # Центроиды: cluster_id → средний нормализованный вектор (для сортировки соседних кластеров).
        if "cluster" in df.columns:
            self._clusters = df["cluster"].values.astype(np.int32)
            for cluster_id in np.unique(self._clusters):
                idx = np.where(self._clusters == cluster_id)[0]
                self._cluster_to_idx[int(cluster_id)] = idx
                centroid = self._vectors_norm[idx].mean(axis=0)
                norm = np.linalg.norm(centroid)
                self._cluster_centroids[int(cluster_id)] = (
                    centroid / norm if norm > 0 else centroid
                )

        # Топ-категории для LLM-объяснений.
        if "top_cats" in df.columns:
            for rk, raw in zip(self._party_rks, df["top_cats"].values):
                try:
                    cats = json.loads(raw) if isinstance(raw, str) else []
                    self._rk_to_top_cats[str(rk)] = cats[:3]
                except Exception:
                    pass

        # PCA и словарь фич для SHAP-атрибуции.
        if PCA_PATH.exists() and VOCAB_PATH.exists():
            with open(PCA_PATH, "rb") as f:
                pca = pickle.load(f)
            with open(VOCAB_PATH, "rb") as f:
                vocab = pickle.load(f)
            self._pca_components = pca.components_.astype(np.float32)
            self._feature_columns = vocab.get("feature_columns", [])

        self._loaded = True

    def get_info(self) -> Dict[str, Any]:
        """Возвращает состояние модели для /health эндпоинта."""
        self._load()
        n_users = len(self._party_rks) if self._party_rks is not None else 0
        n_clusters = len(self._cluster_to_idx)
        n_features = len(self._feature_columns)
        return {
            "model_version": MODEL_VERSION,
            "model_loaded": self._loaded and n_users > 0,
            "users_in_index": int(n_users),
            "clusters": n_clusters,
            "pca_features": n_features,
        }

    def _fallback(self, top_k: int, exclude_rk: Optional[str] = None) -> List[str]:
        assert self._party_rks is not None
        rng = np.random.default_rng()
        mask = np.ones(len(self._party_rks), dtype=bool)
        if exclude_rk is not None:
            mask &= self._party_rks != exclude_rk
        pool = self._party_rks[mask]
        size = min(top_k, len(pool))
        return rng.choice(pool, size=size, replace=False).tolist()

    async def get_recommendations(
        self,
        session: Any,
        party_rk: str,
        top_k: int = 10,
    ) -> List[str]:
        """Возвращает список party_rk ближайших пользователей."""
        scored = await self.get_recommendations_scored(
            session=session, party_rk=party_rk, top_k=top_k
        )
        return [rk for rk, _ in scored]

    def _cosine_top_k(
        self,
        target_vec: np.ndarray,
        target_global_idx: int,
        candidate_idx: np.ndarray,
        top_k: int,
    ) -> List[Tuple[str, float]]:
        """
        Cosine similarity target_vec против подмножества candidate_idx.
        Возвращает до top_k результатов, отсортированных по убыванию.
        """
        assert self._vectors_norm is not None
        assert self._party_rks is not None

        vecs = self._vectors_norm[candidate_idx]
        sims = vecs @ target_vec

        # Исключаем самого target-юзера если он попал в candidate_idx.
        local_self = np.where(candidate_idx == target_global_idx)[0]
        if local_self.size:
            sims[local_self[0]] = -2.0

        k = min(top_k, len(sims))
        top_local = np.argpartition(sims, -k)[-k:]
        top_local = top_local[np.argsort(sims[top_local])[::-1]]

        return [
            (str(self._party_rks[candidate_idx[i]]), float(sims[i])) for i in top_local
        ]

    def _sorted_cluster_ids(
        self, target_vec: np.ndarray, own_cluster_id: int
    ) -> List[int]:
        """
        Возвращает cluster_id, отсортированные по убыванию сходства центроида
        с target_vec. Собственный кластер — первый.
        """
        if not self._cluster_centroids:
            return [own_cluster_id]
        sims = {
            cid: float(centroid @ target_vec)
            for cid, centroid in self._cluster_centroids.items()
        }
        return sorted(sims, key=lambda c: sims[c], reverse=True)

    async def get_recommendations_scored(
        self,
        session: Any,
        party_rk: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        Многоэтапный поиск с расширением по соседним кластерам:

          1. Собираем кандидатов из кластеров по убыванию близости центроида
             к target-юзеру — пока не наберём top_k * CANDIDATE_FACTOR.
          2. Запускаем cosine similarity по всей собранной выборке.
          3. Если кластерного индекса нет — cosine по всем юзерам.

        Роутер применяет SQL-фильтры (пол/возраст/город) ПОСЛЕ этого метода,
        поэтому мы возвращаем top_k * CANDIDATE_FACTOR кандидатов —
        достаточный запас, чтобы после фильтрации осталось top_k.

        Возвращает список (party_rk, cosine_similarity) по убыванию.
        """
        # Множитель запаса: даже если ~70% кандидатов отфильтруются
        # по полу/возрасту/городу, нужное кол-во останется.
        CANDIDATE_FACTOR = 6

        self._load()
        assert self._party_rks is not None
        assert self._vectors_norm is not None

        t_idx = self._rk_to_idx.get(str(party_rk))
        if t_idx is None:
            return [
                (rk, 0.0)
                for rk in self._fallback(
                    top_k * CANDIDATE_FACTOR, exclude_rk=str(party_rk)
                )
            ]

        target_vec = self._vectors_norm[t_idx]
        need = top_k * CANDIDATE_FACTOR

        if not self._cluster_to_idx:
            # Кластерного индекса нет — ищем по всем юзерам.
            all_idx = np.arange(len(self._party_rks))
            return self._cosine_top_k(target_vec, t_idx, all_idx, need)

        # Собираем индексы из кластеров в порядке убывания близости центроида.
        own_cluster = int(self._clusters[t_idx])  # type: ignore[index]
        collected: List[int] = []
        for cid in self._sorted_cluster_ids(target_vec, own_cluster):
            collected.extend(self._cluster_to_idx[cid].tolist())
            if len(collected) >= need:
                break

        candidate_idx = np.array(collected, dtype=np.int64)
        return self._cosine_top_k(target_vec, t_idx, candidate_idx, need)

    def get_top_cats(self, party_rk: str) -> List[str]:
        """Возвращает топ-категории пользователя для LLM-промпта."""
        self._load()
        return self._rk_to_top_cats.get(str(party_rk), [])

    async def explain_match_async(
        self, target_rk: str, candidate_rk: str, score: float
    ) -> str:
        """
        Объяснение мэтча: сначала LLM (с Redis-кэшем), fallback — SHAP.

        Порядок:
          1. Redis-кэш → если есть, сразу возвращаем.
          2. LLM (Qwen через llama-cpp) — промпт содержит SHAP-атрибуцию:
             dominant_label (что именно их объединяет по модели) +
             joint_top_cats (общие категории с наибольшим вкладом).
          3. SHAP-fallback (explain_match) — всегда доступен офлайн.
        """
        from app.services import llm as llm_service
        from app.services import llm_cache

        cached = await llm_cache.get_cached(target_rk, candidate_rk)
        if cached:
            return cached

        dominant_label: str = ""
        joint_top_cats: List[str] = []
        if (
            self._pca_components is not None
            and self._feature_columns
            and self._vectors_raw is not None
        ):
            t_idx = self._rk_to_idx.get(str(target_rk))
            c_idx = self._rk_to_idx.get(str(candidate_rk))
            if t_idx is not None and c_idx is not None:
                dominant_label, joint_top_cats = _extract_shap_info(
                    self._feature_columns,
                    self._vectors_raw[t_idx],
                    self._vectors_raw[c_idx],
                    self._pca_components,
                )

        explanation = await llm_service.generate_explanation(
            dominant_label=dominant_label,
            joint_top_cats=joint_top_cats,
            score=score,
        )
        if explanation:
            await llm_cache.set_cached(target_rk, candidate_rk, explanation)
            return explanation

        return self.explain_match(target_rk, candidate_rk, score)

    def explain_match(self, target_rk: str, candidate_rk: str, score: float) -> str:
        """
        SHAP-like объяснение мэтча на русском языке.

        Восстанавливает приближённые исходные векторы фич из PCA-координат
        обоих пользователей. Поэлементное произведение показывает, какие
        группы фич (категории, временные паттерны, типы мест, токены
        мерчантов) вносят наибольший вклад в их сходство. На основе
        доминирующей группы строится текстовая фраза.

        Деградация: если pca/vocab не загружены или score низкий —
        возвращает нейтральную фразу.
        """
        self._load()

        if (
            self._pca_components is None
            or not self._feature_columns
            or self._vectors_raw is None
        ):
            return random.choice(_TEMPLATES["fallback"])

        t_idx = self._rk_to_idx.get(str(target_rk))
        c_idx = self._rk_to_idx.get(str(candidate_rk))
        if t_idx is None or c_idx is None:
            return random.choice(_TEMPLATES["fallback"])

        return _build_explanation(
            self._feature_columns,
            self._vectors_raw[t_idx],
            self._vectors_raw[c_idx],
            self._pca_components,
            score,
        )
