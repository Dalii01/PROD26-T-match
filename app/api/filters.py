"""
Фильтры кандидатов для ленты мэтчей.

Правила:
  - Пол:    строго противоположный пол. Если пол не заполнен — фильтр снимается.
  - Возраст: несовершеннолетние (< 18) видят только несовершеннолетних.
             Взрослые видят только взрослых в диапазоне ±5 лет.
             Если birth_date не заполнен — фильтр снимается.
  - Город:  совпадение города. Если кандидатов из города < MIN_CITY_RESULTS
             или city не заполнен — фильтр снимается (fallback).

Использование:
    from app.api.filters import strict_conditions, city_conditions, MIN_CITY_RESULTS

    # Обязательные (пол + возраст)
    stmt = stmt.where(*strict_conditions(target_user))

    # Сначала пробуем с городом
    stmt_city = stmt.where(*city_conditions(target_user))
    # Если мало результатов — используем stmt без города
"""

from datetime import date
from typing import List

from sqlalchemy import or_

from app.models.user import User

_MIN_AGE_ADULT = 18
_MAX_AGE_DIFF = 5

# Если после всех фильтров осталось меньше — роутер убирает фильтр города.
MIN_CITY_RESULTS = 3


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _age(birth_date: date) -> int:
    today = date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def gender_conditions(target_user: User) -> list:
    """Строго противоположный пол."""
    if not target_user.gender:
        return []
    return [User.gender != target_user.gender]


def age_conditions(target_user: User) -> list:
    """
    Несовершеннолетние ↔ несовершеннолетние.
    Взрослые ↔ взрослые ±5 лет.
    Кандидаты без birth_date: исключаем если target — несовершеннолетний,
    пропускаем если target — взрослый.
    """
    if not target_user.birth_date:
        return []

    today = date.today()
    adult_cutoff = _shift_years(today, -_MIN_AGE_ADULT)
    target_age = _age(target_user.birth_date)

    if target_age < _MIN_AGE_ADULT:
        # Несовершеннолетний: кандидат тоже < 18 (birth_date позже cutoff).
        # Кандидатов без birth_date не показываем (безопасность).
        return [User.birth_date > adult_cutoff]

    # Взрослый: кандидат взрослый + разница возраста не больше 5 лет.
    bd = target_user.birth_date
    min_bd = _shift_years(bd, -_MAX_AGE_DIFF)  # кандидат не старше target + 5
    max_bd = _shift_years(bd, +_MAX_AGE_DIFF)  # кандидат не моложе target - 5
    return [
        or_(User.birth_date.is_(None), User.birth_date <= adult_cutoff),
        or_(User.birth_date.is_(None), User.birth_date >= min_bd),
        or_(User.birth_date.is_(None), User.birth_date <= max_bd),
    ]


def city_conditions(target_user: User) -> list:
    """Тот же город."""
    if not target_user.city:
        return []
    return [User.city == target_user.city]


def strict_conditions(target_user: User) -> list:
    """Все обязательные фильтры (пол + возраст). Без города — у него есть fallback."""
    return [*gender_conditions(target_user), *age_conditions(target_user)]
