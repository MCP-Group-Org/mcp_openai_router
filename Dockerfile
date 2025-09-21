# ./Dockerfile
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off \
    UVICORN_WORKERS=1 \
    APP_MODULE=app.main:app \
    PORT=8000

# Базовые пакеты (минимум) и пользователь без root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

# Установка зависимостей для MCP/ASGI.
# Официальный SDK MCP: pip install "mcp[cli]"
# FastAPI для ASGI и uvicorn как сервер; fastapi-mcp — опциональный мост FastAPI→MCP.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install fastapi fastapi-mcp "mcp[cli]" "uvicorn[standard]"

# Копируем проект (пока репозиторий пустой — это нормально)
COPY . /app
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# По умолчанию ждём FastAPI-приложение по пути app.main:app
CMD ["sh", "-lc", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
