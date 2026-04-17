FROM python:3.11

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock /app/

RUN poetry install --no-interaction --no-ansi --no-root

COPY app /app/app
COPY admin-frontend /app/admin-frontend
COPY alembic /app/alembic
COPY alembic.ini /app/
COPY seed.py /app/seed.py
COPY scripts /app/scripts
COPY entrypoint.sh /app/entrypoint.sh

# Создаём пустые папки — при деплое сюда монтируются data/ и models/ с хоста
RUN mkdir -p /app/data /app/models

CMD ["/bin/sh", "/app/entrypoint.sh"]
