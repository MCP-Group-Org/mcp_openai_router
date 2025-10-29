# ./Dockerfile
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off \
    UVICORN_WORKERS=1 \
    APP_MODULE=app.main:app \
    OPENAI_BASE_URL=https://api.openai.com/v1

# Пакеты и юзер
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

# Зависимости
COPY requirements.txt /app/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# Код
COPY . /app
RUN chown -R appuser:appuser /app
USER appuser

# Порт задаётся переменной окружения PORT (обязательной для запуска)
CMD ["sh", "-lc", "uvicorn ${APP_MODULE:-app.main:app} --host 0.0.0.0 --port ${PORT}"]
