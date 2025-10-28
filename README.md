# MCP OpenAI Router

Минимальный HTTP-роутер для протокола Model Context Protocol (MCP), проксирующий вызовы инструментов и OpenAI Responses API.

## Основные модули

- `app/main.py` — точка входа FastAPI, регистрирует инструменты и подключает API-роутер.
- `app/api/routes.py` — реализации JSON-RPC методов (`initialize`, `tools/list`, `tools/call`, `shutdown`, `ping`) и HTTP-эндпоинтов `/health`, `/mcp`.
- `app/tools/registry.py` и `app/tools/handlers.py` — описание схем MCP-инструментов и их обработчики.
- `app/services/openai_responses.py` — обёртки над OpenAI Responses API, нормализация ответов и валидаторы аргументов.
- `app/core/config.py` и `app/core/session.py` — глобальные настройки и хранилище активных сессий.

## Локальный запуск

```bash
uvicorn app.main:app --reload --port 8080
```

Перед запуском экспортируйте переменные окружения:

- `OPENAI_API_KEY` — API-ключ OpenAI (обязателен для инструмента `chat`).
- `MCP_REQUIRE_SESSION=false` — опционально для тестирования без предварительной авторизации.

## Тестирование

```bash
python -m pytest
```

Тесты monkeypatch-ят фабрику клиента OpenAI (`app.main._create_openai_client`), поэтому внешние сетевые вызовы не выполняются.

## Структура репозитория

- `app/` — исходный код сервиса и вспомогательных слоёв.
- `tests/` — pytest-сценарии MCP-маршрутов.
- `deploy/` — шаблоны ingress и файлы окружения для развёртывания.
- `docker-compose*.yaml` — настройки локального и production-стека.
