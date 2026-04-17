"""
Async HTTP клиент к llama-cpp-python OpenAI-совместимому серверу.

Используется для генерации объяснений мэтчей на русском языке через Qwen.
При любой ошибке (таймаут, сервис недоступен, LLM_ENABLED=false) возвращает None —
роутер в этом случае использует SHAP-fallback из baseline.py.

Переменные окружения:
  LLM_URL      — базовый URL сервера (default: http://llm:8080)
  LLM_ENABLED  — "true" / "false"  (default: true)
  LLM_TIMEOUT  — секунды ожидания   (default: 8.0)
"""

import os
from typing import List, Optional

import httpx

LLM_URL: str = os.getenv("LLM_URL", "http://llm:8080")
LLM_ENABLED: bool = os.getenv("LLM_ENABLED", "true").lower() == "true"
LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "8.0"))

_SYSTEM = (
    "Ты помощник приложения знакомств. "
    "Пиши только на русском языке, без английских слов и кавычек."
)

_PROMPT_TEMPLATE = """\
Два человека совпали в приложении знакомств ({score:.0%} схожести).
Их объединяет: {dominant}.
Напиши одну фразу 8-12 слов почему им стоит познакомиться.
Начни с "похоже,", "вас объединяет" или "у вас". Только русский язык.\
"""


async def generate_explanation(
    dominant_label: str,
    joint_top_cats: List[str],
    score: float,
) -> Optional[str]:
    """
    Генерирует объяснение мэтча через LLM.

    dominant_label  — что именно объединяет пользователей по SHAP-атрибуции
                      (например "общие категории покупок" или "похожий ритм жизни").
    joint_top_cats  — топ-категории с наибольшим совместным вкладом.
    score           — cosine similarity в PCA-пространстве.

    Возвращает None при любой ошибке — вызывающий код использует SHAP-fallback.
    """
    if not LLM_ENABLED:
        return None

    dominant = dominant_label or "общие интересы"

    payload = {
        "model": "qwen",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": _PROMPT_TEMPLATE.format(
                    score=score,
                    dominant=dominant,
                ),
            },
        ],
        "max_tokens": 25,
        "temperature": 0.7,
        "stop": ["\n", ".", "!"],
    }

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{LLM_URL}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return text if text else None
    except Exception:
        return None
