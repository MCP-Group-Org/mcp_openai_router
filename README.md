# MCP OpenAI Router

Минимальный HTTP-роутер для протокола Model Context Protocol (MCP), проксирующий вызовы инструментов к OpenAI Responses API с поддержкой hosted-инструментов (например, `web_search`) и опциональным внешним инструментом `think` для захвата промежуточных рассуждений модели.

## Возможности

- **OpenAI Responses API** — интеграция с поллингом, нормализацией и обработкой ошибок
- **Think Tool** — опциональный внешний MCP-сервер для захвата рассуждений модели
- **LangSmith Tracing** — интеграция наблюдаемости для трассировки вызовов
- **Session Management** — управление сессиями MCP-клиентов
- **JSON-RPC 2.0** — полная поддержка протокола MCP

## Быстрый старт

### Разработка в Docker (рекомендуется)

```bash
# Сборка и запуск
docker-compose up --build

# Проверка здоровья (доступно только внутри docker-сети)
# http://mcp-openai-router:8080/mcp
```

### Локальный запуск

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера
uvicorn app.main:app --reload --port 8080
```

## Конфигурация

### Обязательные переменные

- `OPENAI_API_KEY` — API-ключ OpenAI

### Опциональные переменные

#### Управление сессиями

- `MCP_REQUIRE_SESSION` — требовать явную инициализацию сессии (по умолчанию `true`)

#### OpenAI Responses API

- `POLL_DELAY` — задержка между опросами статуса (секунды, по умолчанию `1.0`)
- `MAX_POLLS` — максимальное количество попыток опроса (по умолчанию `60`)
- `RESPONSES_POLL_MAX_CONCURRENCY` — максимальное количество параллельных опросов (по умолчанию `8`)

#### Think Tool

- `THINK_TOOL_ENABLED` — включить Think Tool (`0` или `1`, по умолчанию `0`)
- `THINK_TOOL_URL` — URL внешнего MCP-сервера для Think Tool
- `THINK_TOOL_TIMEOUT_MS` — таймаут запросов к Think Tool (миллисекунды)
- `THINK_TOOL_RETRY_LIMIT` — максимальное количество повторных попыток

#### LangSmith

- `LANGSMITH_TRACING` — включить трассировку LangSmith (`0` или `1`, по умолчанию `0`)
- `LANGSMITH_PROJECT` — имя проекта в LangSmith (опционально)
- `LANGSMITH_API_KEY` — API-ключ LangSmith

## Тестирование

```bash
# Запуск всех тестов
python -m pytest

# Запуск конкретного теста
python -m pytest tests/test_mcp_router.py::test_initialize_list_and_chat

# Запуск с подробным выводом
python -m pytest -v
```

Тесты используют monkeypatching для мокирования клиента OpenAI, поэтому внешние сетевые вызовы не выполняются.

## Структура проекта

### Основные модули

- `app/main.py` — точка входа FastAPI, реестр инструментов, обработчик `chat`
- `app/api/routes.py` — JSON-RPC методы MCP и HTTP-эндпоинты
- `app/tools/registry.py` — определения схем инструментов (`ToolSpec`, `ToolSchema`)
- `app/tools/handlers.py` — обработчики простых инструментов (`echo`, `read_file`)
- `app/services/openai_responses.py` — адаптер OpenAI Responses API
- `app/services/think_processor.py` — обработка Think Tool
- `app/services/langsmith_tracing.py` — интеграция LangSmith
- `app/think_client.py` — HTTP-клиент для внешнего Think Tool MCP-сервера
- `app/core/config.py` — глобальная конфигурация
- `app/core/session.py` — хранилище активных сессий
- `app/models/json_rpc.py` — Pydantic-модели для JSON-RPC
- `app/utils/metadata.py` — утилиты сериализации метаданных

### Директории

- `app/` — исходный код приложения
- `tests/` — pytest-сценарии
- `deploy/` — шаблоны ingress и файлы окружения для продакшена
- `docker-compose*.yaml` — конфигурации Docker-стека

## Документация

- `CLAUDE.md` — руководство для Claude Code по работе с проектом
- `AGENTS.md` — инструкции для контрибьюторов и формат коммитов

## Архитектура

### Поток обработки запроса

1. **JSON-RPC Entry** (`app/api/routes.py`) → обработка MCP-методов
2. **Tool Handlers** (`app/main.py`, `app/tools/handlers.py`) → выполнение логики инструментов
3. **OpenAI Integration** (`app/services/openai_responses.py`) → работа с Responses API
4. **Think Tool Processing** (`app/services/think_processor.py`) → обработка think-вызовов
5. **LangSmith Tracing** (`app/services/langsmith_tracing.py`) → трассировка и наблюдаемость

### Обработчик Chat (`_handle_chat`)

Основной оркестратор для chat-completions:

1. Валидация параметров (`model`, `messages`)
2. Инициализация клиента OpenAI
3. Построение запроса с авто-инъекцией `think` tool
4. Отправка запроса в Responses API
5. Поллинг статуса до терминального состояния
6. Обработка Think Tool вызовов (если есть)
7. Follow-up запросы с результатами think
8. Итерация до `max_turns=15`
9. Сборка финального ответа с метаданными
10. Обёртка в LangSmith run (если включено)

## Язык и коммуникация

- Все комментарии к коду, описания коммитов и документация должны быть на русском языке
- Заголовки коммитов — на английском языке

## Лицензия

См. `AGENTS.md` для деталей контрибуции.
