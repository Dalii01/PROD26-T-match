from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.api.filters import MIN_CITY_RESULTS, city_conditions, strict_conditions
from app.services import recommendations_service

_recommender = recommendations_service._recommender
get_daily_count = recommendations_service.get_daily_count

router = APIRouter()


@router.get("")
async def get_recommendations(
    request: Request,
    top_k: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Возвращает список рекомендованных пользователей.

    Фильтры (применяются всегда):
      - Пол: строго противоположный.
      - Возраст: несовершеннолетние ↔ несовершеннолетние; взрослые ↔ взрослые ±5 лет.
      - Город: совпадение города; если мало кандидатов — fallback без фильтра города.
    """
    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }
    try:
        user_id = int(raw_user_id)
    except ValueError:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_INVALID",
                "message": "X-User-ID must be integer",
            },
        }

    return await recommendations_service._get_recommendations(
        session=session,
        user_id=user_id,
        top_k=top_k,
        recommender=_recommender,
        get_daily_count_fn=get_daily_count,
        strict_conditions_fn=strict_conditions,
        city_conditions_fn=city_conditions,
        min_city_results=MIN_CITY_RESULTS,
    )
