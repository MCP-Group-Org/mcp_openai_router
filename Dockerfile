# ./Dockerfile
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off \
    UVICORN_WORKERS=1 \
    APP_MODULE=app.main:app \
    ROUTER_PORT=8000 \
    OPENAI_BASE_URL=https://api.openai.com/v1

# Пакеты и юзер
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

# Зависимости
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install fastapi fastapi-mcp "mcp[cli]" openai "uvicorn[standard]" pydantic-settings

# Код
COPY . /app
RUN chown -R appuser:appuser /app
USER appuser

# Слушаем тот же порт, что в .env (8000)
EXPOSE 8000

# Безопасные дефолты на случай отсутствия .env
CMD ["sh", "-lc", "uvicorn ${APP_MODULE:-app.main:app} --host 0.0.0.0 --port ${ROUTER_PORT:-8000}"]
