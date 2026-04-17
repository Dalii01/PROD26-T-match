"""
Standalone-тест бейзлайн-рекомендера.

Источник данных: app/transaction_600_new.csv (те же данные, что seed.py заливает в БД).
Запуск: python -m app.model.test_baseline  (из корня проекта)
"""

import csv
import math
import os
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

# Принудительно UTF-8 для вывода (нужно на Windows)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Пулы, идентичные seed.py
# ---------------------------------------------------------------------------
MALE_FIRST = ["Алексей", "Иван", "Никита", "Павел", "Тимур", "Егор", "Максим", "Денис", "Кирилл", "Артем"]
FEMALE_FIRST = ["Дарья", "Мария", "Ольга", "София", "Юлия", "Алина", "Анастасия", "Елена", "Полина", "Валерия"]
MALE_LAST = ["Иванов", "Смирнов", "Соколов", "Попов", "Морозов", "Орлов", "Николаев", "Соловьев", "Степанов", "Зайцев"]
FEMALE_LAST = ["Петрова", "Кузнецова", "Новикова", "Лебедева", "Волкова", "Михайлова", "Федорова", "Гусева", "Виноградова", "Белова"]
CITIES = ["Москва", "Санкт-Петербург", "Казань", "Нижний Новгород", "Екатеринбург", "Новосибирск", "Самара", "Краснодар", "Воронеж", "Уфа"]
TAGS = ["кофе", "путешествия", "спорт", "кино", "книги", "музыка", "рестораны", "игры", "природа", "искусство"]
BIRTH_YEARS = list(range(1990, 2013))  # возраст 14–36


def _mock_user_meta(party_rk: str) -> dict[str, Any]:
    """Детерминированно генерирует метаданные пользователя по его party_rk."""
    rng = random.Random(party_rk)
    gender = rng.choice(["male", "female"])
    if gender == "male":
        first_name = rng.choice(MALE_FIRST)
        last_name = rng.choice(MALE_LAST)
    else:
        first_name = rng.choice(FEMALE_FIRST)
        last_name = rng.choice(FEMALE_LAST)
    birth_year = rng.choice(BIRTH_YEARS)
    birth_date = date(birth_year, rng.randint(1, 12), rng.randint(1, 28))
    age = date.today().year - birth_year - (
        (date.today().month, date.today().day) < (birth_date.month, birth_date.day)
    )
    tags = rng.sample(TAGS, 3)
    city = rng.choice(CITIES)
    return {
        "party_rk": party_rk,
        "name": f"{first_name} {last_name}",
        "gender": gender,
        "city": city,
        "birth_date": birth_date,
        "age": age,
        "tags": tags,
    }


# ---------------------------------------------------------------------------
# Чтение CSV и подготовка ML-профилей
# ---------------------------------------------------------------------------

def _load_profiles_from_csv(
    csv_path: Path,
    max_users: int = 30,
    min_transactions: int = 5,
) -> dict[str, dict]:
    """
    Читает CSV транзакций и возвращает dict:
      party_rk -> {
          "total_transactions": int,
          "category_shares": {category: share},
          "sample_transactions": [(merchant_nm, category_nm), ...]  # до 5 шт.
      }
    """
    cat_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    samples: dict[str, list[tuple[str, str]]] = defaultdict(list)

    with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rk = row.get("party_rk", "").strip()
            cat = row.get("category_nm", "").strip() or "Другое"
            merchant = row.get("merchant_nm", "").strip() or "—"
            if not rk:
                continue
            cat_counts[rk][cat] += 1
            totals[rk] += 1
            if len(samples[rk]) < 5:
                samples[rk].append((merchant, cat))

    # Оставляем только пользователей с достаточным кол-вом транзакций
    eligible = [rk for rk, total in totals.items() if total >= min_transactions]
    selected = eligible[:max_users]

    profiles: dict[str, dict] = {}
    for rk in selected:
        total = totals[rk]
        shares = {
            cat: round(count / total, 4)
            for cat, count in cat_counts[rk].items()
        }
        profiles[rk] = {
            "total_transactions": total,
            "category_shares": shares,
            "sample_transactions": samples[rk],
        }
    return profiles


# ---------------------------------------------------------------------------
# Метрики сходства (идентичны production-коду)
# ---------------------------------------------------------------------------

def _cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    if not v1 or not v2:
        return 0.0
    dot = sum(float(v1.get(k, 0)) * float(v) for k, v in v2.items())
    norm1 = math.sqrt(sum(x ** 2 for x in v1.values()))
    norm2 = math.sqrt(sum(x ** 2 for x in v2.values()))
    if norm1 <= 0 or norm2 <= 0:
        return 0.0
    return dot / (norm1 * norm2)


def _jaccard_similarity(tags1: list[str], tags2: list[str]) -> float:
    if not tags1 or not tags2:
        return 0.0
    s1, s2 = set(tags1), set(tags2)
    union = s1 | s2
    return len(s1 & s2) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# Рекомендер (offline, без БД)
# ---------------------------------------------------------------------------

def baseline_recommend(
    target_rk: str,
    profiles: dict[str, dict],
    top_k: int = 5,
) -> list[dict]:
    """BaselineRecommender: cosine по category_shares."""
    target_vec = profiles[target_rk]["category_shares"]
    scores = []
    for rk, prof in profiles.items():
        if rk == target_rk:
            continue
        score = _cosine_similarity(target_vec, prof["category_shares"])
        scores.append({"party_rk": rk, "score": score})
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_k]


def hybrid_recommend(
    target_rk: str,
    profiles: dict[str, dict],
    users_meta: dict[str, dict],
    top_k: int = 5,
    w_trans: float = 0.7,
    w_int: float = 0.3,
) -> list[dict]:
    """HybridRecommender: cosine × 0.7 + jaccard(tags) × 0.3, с возрастным фильтром."""
    target_vec = profiles[target_rk]["category_shares"]
    target_meta = users_meta[target_rk]
    target_age = target_meta["age"]
    target_tags = target_meta["tags"]
    is_adult = target_age >= 18

    scores = []
    for rk, prof in profiles.items():
        if rk == target_rk:
            continue
        cand_meta = users_meta[rk]
        cand_age = cand_meta["age"]

        # Возрастной фильтр
        if is_adult and cand_age < 18:
            continue
        if not is_adult and cand_age >= 18:
            continue
        age_diff = abs(target_age - cand_age)
        if age_diff > 5:
            continue

        trans_score = _cosine_similarity(target_vec, prof["category_shares"])
        int_score = _jaccard_similarity(target_tags, cand_meta["tags"])
        final = w_trans * trans_score + w_int * int_score

        scores.append({
            "party_rk": rk,
            "name": cand_meta["name"],
            "age": cand_age,
            "city": cand_meta["city"],
            "tags": cand_meta["tags"],
            "score": round(final, 4),
            "trans_score": round(trans_score, 4),
            "int_score": round(int_score, 4),
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_k]


# ---------------------------------------------------------------------------
# Вывод результатов
# ---------------------------------------------------------------------------

def _print_user(meta: dict, profile: dict) -> None:
    print(f"  Имя:           {meta['name']}")
    print(f"  Пол:           {'Муж' if meta['gender'] == 'male' else 'Жен'}")
    print(f"  Возраст:       {meta['age']} лет  ({meta['birth_date']})")
    print(f"  Город:         {meta['city']}")
    print(f"  Интересы:      {', '.join(meta['tags'])}")
    print(f"  Транзакций:    {profile['total_transactions']}")
    top_cats = sorted(profile["category_shares"].items(), key=lambda x: x[1], reverse=True)[:5]
    print("  Топ категорий трат:")
    for cat, share in top_cats:
        bar = "█" * int(share * 20)
        print(f"    {cat:<25} {bar} {share:.1%}")
    print("  Примеры транзакций:")
    for merchant, cat in profile["sample_transactions"][:5]:
        print(f"    • {merchant}  [{cat}]")


def _print_recommendations(recs: list[dict], label: str) -> None:
    print(f"\n  ── {label} ──")
    if not recs:
        print("  (нет подходящих кандидатов)")
        return
    for i, r in enumerate(recs, 1):
        print(
            f"  {i}. {r['name']:<22}  {r['age']} л.  {r['city']:<20}"
            f"  score={r['score']:.4f}"
            f"  (trans={r['trans_score']:.3f}, tags={r['int_score']:.3f})"
        )
        print(f"     интересы: {', '.join(r['tags'])}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def run() -> None:
    csv_path = Path(__file__).parent.parent / "transaction_600_new.csv"
    if not csv_path.exists():
        print(f"[ERROR] CSV не найден: {csv_path}")
        return

    sep = "=" * 70
    print(sep)
    print("  ТЕСТ БЕЙЗЛАЙН-РЕКОМЕНДЕРА (offline, без БД)")
    print(f"  Источник: {csv_path.name}")
    print(sep)

    print("\nЧтение CSV и подготовка профилей...")
    profiles = _load_profiles_from_csv(csv_path, max_users=30, min_transactions=5)
    if len(profiles) < 3:
        print("[ERROR] Недостаточно пользователей в CSV")
        return
    print(f"Загружено {len(profiles)} пользователей из CSV.\n")

    # Мета-данные для всех пользователей
    users_meta = {rk: _mock_user_meta(rk) for rk in profiles}

    # Берём 2 целевых пользователя
    party_rks = list(profiles.keys())
    targets = [party_rks[0], party_rks[2]]

    for target_rk in targets:
        meta = users_meta[target_rk]
        profile = profiles[target_rk]
        short_id = target_rk[:12] + "..."

        print("+" + "-" * 68 + "+")
        print(f"|  ПОЛЬЗОВАТЕЛЬ: {short_id:<52}|")
        print("+" + "-" * 68 + "+")
        _print_user(meta, profile)

        # Baseline (только транзакции)
        baseline_recs = baseline_recommend(target_rk, profiles, top_k=5)
        baseline_with_meta = []
        for r in baseline_recs:
            m = users_meta[r["party_rk"]]
            baseline_with_meta.append({
                "party_rk": r["party_rk"],
                "name": m["name"],
                "age": m["age"],
                "city": m["city"],
                "tags": m["tags"],
                "score": round(r["score"], 4),
                "trans_score": round(r["score"], 4),
                "int_score": 0.0,
            })
        _print_recommendations(baseline_with_meta, "Baseline (cosine по транзакциям)")

        # Hybrid (транзакции + интересы + возрастной фильтр)
        hybrid_recs = hybrid_recommend(target_rk, profiles, users_meta, top_k=5)
        _print_recommendations(hybrid_recs, "Hybrid (0.7×trans + 0.3×tags + возраст ±5 лет)")

        print()


if __name__ == "__main__":
    run()
