#!/usr/bin/env sh

set -e

# Wait briefly for DB and apply migrations on startup
max_attempts=10
attempt=1
export PYTHONPATH="/app"

while [ "$attempt" -le "$max_attempts" ]; do
  if alembic -c /app/alembic.ini upgrade head; then
    break
  fi

  echo "alembic upgrade failed (attempt $attempt/$max_attempts). retrying in 3s..." >&2
  attempt=$(($attempt+1))
  sleep 3

  if [ "$attempt" -gt "$max_attempts" ]; then
    echo "alembic upgrade failed after $max_attempts attempts" >&2
    exit 1
  fi
done

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
