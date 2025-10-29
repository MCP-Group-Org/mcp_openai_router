# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MCP OpenAI Router** is a minimal HTTP router implementing the Model Context Protocol (MCP). It proxies tool calls to the OpenAI Responses API, supporting hosted tools (e.g., `web_search`) and an optional external `think` tool for capturing intermediate reasoning.

## Language and Communication

**All code comments, commit messages (descriptions), and documentation must be in Russian.** However, commit message titles should be in English. See `AGENTS.md` for detailed contributor instructions and commit message format.

## Common Development Commands

### Development is done in a Docker container

### Testing
```bash
# Run all tests
python -m pytest

# Run specific test
python -m pytest tests/test_mcp_router.py::test_initialize_list_and_chat

# Run with verbose output
python -m pytest -v
```

Tests use monkeypatching to mock the OpenAI client factory (`app.main._create_openai_client`), so no external network calls are made.

### Docker

```bash
# Build and run with docker-compose
docker-compose up --build

# Check service health - not possible locally, server is deployed in internal docker network
http://mcp-openai-router:8080/mcp
```

## Architecture

### Request Flow
1. **JSON-RPC Entry** (`app/api/routes.py`): Handles MCP protocol methods (`initialize`, `tools/list`, `tools/call`, `shutdown`, `ping`) via `/mcp` endpoint.
2. **Tool Handlers** (`app/main.py`, `app/tools/handlers.py`): Execute tool logic. The `chat` handler is the main integration point with OpenAI Responses API.
3. **OpenAI Integration** (`app/services/openai_responses.py`): Wraps OpenAI Responses API with polling, normalization, and error handling.
4. **Think Tool Processing** (`app/services/think_processor.py`, `app/think_client.py`): Handles the special `think` tool, which captures model reasoning to an external MCP server.
5. **LangSmith Tracing** (`app/services/langsmith_tracing.py`): Optional observability integration that creates traces from metadata passed in tool calls.

### Key Modules

- **`app/main.py`**: FastAPI app entry point, tool registry, and the `_handle_chat` function (the core orchestrator for chat completions).
- **`app/api/routes.py`**: JSON-RPC method handlers and FastAPI routes.
- **`app/tools/registry.py`**: Tool schema definitions (`ToolSpec`, `ToolSchema`) and the `TOOLS` dictionary.
- **`app/tools/handlers.py`**: Simple tool handlers (`echo`, `read_file`).
- **`app/services/openai_responses.py`**: OpenAI client adapter, response polling, and normalization functions.
- **`app/services/langsmith_tracing.py`**: LangSmith tracer creation and lifecycle management.
- **`app/services/think_processor.py`**: Logic for filtering and processing `think` tool calls, returning follow-up inputs for Responses API.
- **`app/think_client.py`**: HTTP client for external think-tool MCP server (performs handshake and `tools/call`).
- **`app/core/config.py`**: Global configuration from environment variables.
- **`app/core/session.py`**: In-memory session storage (`ACTIVE_SESSIONS`).
- **`app/models/json_rpc.py`**: Pydantic models for JSON-RPC messages.
- **`app/utils/metadata.py`**: Utilities for serializing/deserializing metadata for transport through OpenAI API.

### Chat Handler (`_handle_chat`) Flow

The `chat` tool is the main feature and follows this flow:

1. **Validation**: Extract and validate `model`, `messages`, and optional parameters from tool arguments.
2. **Client Setup**: Lazily initialize `OpenAIClientAdapter`, verify Responses API availability.
3. **Request Building**: Construct payload for `responses.create`, auto-injecting `think` tool definition if enabled.
4. **Initial Call**: Send request to OpenAI Responses API.
5. **Polling**: If status is `queued` or `in_progress`, poll via `responses.retrieve` until terminal status (controlled by `POLL_DELAY` and `MAX_POLLS` from `app/core/config.py`).
6. **Think Tool Processing**: If model requests `think` tool calls, execute them via `ThinkToolProcessor` → `ThinkToolClient` → external MCP server. Collect logs and prepare follow-up inputs.
7. **Follow-up Requests**: If think results exist, send follow-up request with `previous_response_id` and `function_call_output` inputs.
8. **Iteration**: Repeat polling and think processing up to `max_turns=15` iterations (guardrail against infinite loops).
9. **Response Assembly**: Normalize content and tool calls, attach metadata (usage, finish reason, response ID, LangSmith trace info), return `ToolResponse`.
10. **LangSmith Tracing**: Wrap the entire flow in a LangSmith run if enabled via environment or metadata.

### Think Tool Integration

- **Configuration**: `THINK_TOOL_ENABLED=1` and `THINK_TOOL_URL=<endpoint>` in environment.
- **Auto-injection**: If enabled, the `think` tool schema is automatically added to `tools` array in Responses API requests.
- **Execution**: When model calls `think`, router invokes external MCP server via `ThinkToolClient.capture_thought`.
- **Metadata**: Think tool accepts `parent_trace_id` for LangSmith tracing integration.
- **Logging**: All think tool invocations are recorded in `ThinkLogEntry` and attached to final response under `metadata.thinkTool`.

### LangSmith Tracing

- **Activation**: Enabled by `LANGSMITH_TRACING=1` or by passing `metadata.langsmith` with `parent_run_id`, `trace_id`, or `enabled=true` in tool call.
- **Lifecycle**: `LangSmithTracer.start()` creates a run; `finalize_success()` or `finalize_error()` updates it with outputs/errors.
- **Metadata Serialization**: `langsmith` metadata field is serialized to JSON string before sending to OpenAI and deserialized on return (see `serialise_metadata_for_openai` / `deserialise_metadata_from_openai` in `app/utils/metadata.py`).
- **Response Attachment**: Run IDs, trace IDs, and project info are attached to `metadata.langsmith` in tool response.

## Coding Conventions

- **Python 3.12 + FastAPI**, 4-space indentation, PEP 8 import order, full type annotations.
- **Naming**: `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants, Pydantic models use `CamelCase`.
- **Tool Registration**: New tools must be added to `TOOLS` dict in `app/tools/registry.py` and their handlers to `TOOL_HANDLERS` in `app/main.py`.

## Testing Conventions

- **Location**: Add tests to `tests/test_mcp_router.py`.
- **Naming**: `test_<behavior>` (e.g., `test_initialize_list_and_chat`).
- **Fixtures**: Use `clear_sessions` autouse fixture to reset `ACTIVE_SESSIONS`.
- **Mocking**: Mock OpenAI client by monkeypatching `app.main._create_openai_client` with a `DummyOpenAIClient` returning `DummyResponse` objects.
- **Assertions**: Validate JSON-RPC structure, MCP metadata fields (`toolCalls`, `isError`, `content`), and response status codes.

## Important Configuration

- **Session Management**: `MCP_REQUIRE_SESSION=false` allows auto-creation of sessions for testing. In production, set to `true`.
- **Responses Polling**: Adjust `POLL_DELAY` (seconds between polls) and `MAX_POLLS` (max attempts) via environment.
- **Concurrency**: `RESPONSES_POLL_MAX_CONCURRENCY` controls semaphore for parallel polling (default 8).
- **Think Tool**: `THINK_TOOL_ENABLED`, `THINK_TOOL_URL`, `THINK_TOOL_TIMEOUT_MS`, `THINK_TOOL_RETRY_LIMIT`.
- **LangSmith**: `LANGSMITH_TRACING=1`, `LANGSMITH_PROJECT=<name>` (optional), `LANGSMITH_API_KEY` (from langsmith SDK).

## File Structure Notes

- **`deploy/`**: Ingress templates and environment files for production deployment.
- **`app/`**: All application code.
- **`tests/`**: pytest scenarios.
- **`requirements.txt`**: Python dependencies with version constraints.
- **`docker-compose*.yaml`**: Local and production Docker stack configurations.
- **`.env`**: Environment variables (not committed; see `.env.example` if present).
- **`.dockerignore`**: Files excluded from Docker build context.

## Development Workflow

1. **Start with MVP**: Per `AGENTS.md`, begin with a basic solution without premature optimizations.
2. **Task Files**: Create `[task_name].md` files for concrete tasks (implementation, analysis), including problem statement, approach, and checklist. Do not create task files for simple questions or commit documentation requests.
3. **Commit Format**: `<full_branch_name>.<title_in_english>` with description in Russian. See `AGENTS.md` for details.
4. **Testing**: Run pytest after changes to ensure no regressions. Mock external dependencies.

---

# 🇷🇺 РУССКАЯ ВЕРСИЯ / RUSSIAN VERSION

---

Этот файл содержит руководство для Claude Code (claude.ai/code) при работе с кодом в этом репозитории.

## Обзор проекта

**MCP OpenAI Router** — это минимальный HTTP-роутер, реализующий протокол Model Context Protocol (MCP). Он проксирует вызовы инструментов к OpenAI Responses API, поддерживая hosted-инструменты (например, `web_search`) и опциональный внешний инструмент `think` для захвата промежуточных рассуждений модели.

## Язык и коммуникация

**Все комментарии к коду, описания коммитов и документация должны быть на русском языке.** Однако заголовки сообщений коммитов должны быть на английском. См. `AGENTS.md` для подробных инструкций для контрибьюторов и формата сообщений коммитов.

## Основные команды для разработки

### Разработка ведется в контейнере docker


### Тестирование

```bash
# Запуск всех тестов
python -m pytest

# Запуск конкретного теста
python -m pytest tests/test_mcp_router.py::test_initialize_list_and_chat

# Запуск с подробным выводом
python -m pytest -v
```

Тесты используют monkeypatching для мокирования фабрики клиента OpenAI (`app.main._create_openai_client`), поэтому внешние сетевые вызовы не выполняются.

### Docker

```bash
# Сборка и запуск с docker-compose
docker-compose up --build

# Проверка здоровья сервиса, локально невозможна, сервер разворачивается во внутренней docker-сети 
http://mcp-openai-router:8080/mcp

## Архитектура

### Поток обработки запроса

1. **Точка входа JSON-RPC** (`app/api/routes.py`): Обрабатывает методы протокола MCP (`initialize`, `tools/list`, `tools/call`, `shutdown`, `ping`) через эндпоинт `/mcp`.
2. **Обработчики инструментов** (`app/main.py`, `app/tools/handlers.py`): Выполняют логику инструментов. Обработчик `chat` — основная точка интеграции с OpenAI Responses API.
3. **Интеграция с OpenAI** (`app/services/openai_responses.py`): Оборачивает OpenAI Responses API с поллингом, нормализацией и обработкой ошибок.
4. **Обработка Think-инструмента** (`app/services/think_processor.py`, `app/think_client.py`): Обрабатывает специальный инструмент `think`, который захватывает рассуждения модели на внешний MCP-сервер.
5. **Трассировка LangSmith** (`app/services/langsmith_tracing.py`): Опциональная интеграция наблюдаемости, создающая трассы из метаданных, переданных в вызовах инструментов.

### Ключевые модули

- **`app/main.py`**: Точка входа FastAPI-приложения, реестр инструментов и функция `_handle_chat` (центральный оркестратор для chat-completions).
- **`app/api/routes.py`**: Обработчики JSON-RPC методов и маршруты FastAPI.
- **`app/tools/registry.py`**: Определения схем инструментов (`ToolSpec`, `ToolSchema`) и словарь `TOOLS`.
- **`app/tools/handlers.py`**: Простые обработчики инструментов (`echo`, `read_file`).
- **`app/services/openai_responses.py`**: Адаптер клиента OpenAI, поллинг ответов и функции нормализации.
- **`app/services/langsmith_tracing.py`**: Создание трейсера LangSmith и управление жизненным циклом.
- **`app/services/think_processor.py`**: Логика фильтрации и обработки вызовов инструмента `think`, возврат follow-up входов для Responses API.
- **`app/think_client.py`**: HTTP-клиент для внешнего think-tool MCP-сервера (выполняет handshake и `tools/call`).
- **`app/core/config.py`**: Глобальная конфигурация из переменных окружения.
- **`app/core/session.py`**: In-memory хранилище сессий (`ACTIVE_SESSIONS`).
- **`app/models/json_rpc.py`**: Pydantic-модели для JSON-RPC сообщений.
- **`app/utils/metadata.py`**: Утилиты для сериализации/десериализации метаданных для транспорта через OpenAI API.

### Поток работы Chat-обработчика (`_handle_chat`)

Инструмент `chat` — основная функциональность, работающая по следующему алгоритму:

1. **Валидация**: Извлечение и валидация `model`, `messages` и опциональных параметров из аргументов инструмента.
2. **Настройка клиента**: Ленивая инициализация `OpenAIClientAdapter`, проверка доступности Responses API.
3. **Построение запроса**: Формирование payload для `responses.create`, автоматическое внедрение определения инструмента `think`, если включено.
4. **Начальный вызов**: Отправка запроса к OpenAI Responses API.
5. **Поллинг**: Если статус `queued` или `in_progress`, выполняется опрос через `responses.retrieve` до терминального статуса (контролируется `POLL_DELAY` и `MAX_POLLS` из `app/core/config.py`).
6. **Обработка Think-инструмента**: Если модель запрашивает вызовы инструмента `think`, они выполняются через `ThinkToolProcessor` → `ThinkToolClient` → внешний MCP-сервер. Собираются логи и подготавливаются follow-up входы.
7. **Follow-up запросы**: Если есть результаты think, отправляется follow-up запрос с `previous_response_id` и `function_call_output` входами.
8. **Итерация**: Повторение поллинга и обработки think до `max_turns=15` итераций (защита от бесконечных циклов).
9. **Сборка ответа**: Нормализация контента и вызовов инструментов, прикрепление метаданных (usage, finish reason, response ID, LangSmith trace info), возврат `ToolResponse`.
10. **Трассировка LangSmith**: Обёртка всего потока в LangSmith run, если включено через окружение или метаданные.

### Интеграция Think-инструмента

- **Конфигурация**: `THINK_TOOL_ENABLED=1` и `THINK_TOOL_URL=<endpoint>` в окружении.
- **Автоматическое внедрение**: Если включено, схема инструмента `think` автоматически добавляется в массив `tools` в запросах Responses API.
- **Выполнение**: Когда модель вызывает `think`, роутер обращается к внешнему MCP-серверу через `ThinkToolClient.capture_thought`.
- **Метаданные**: Think-инструмент принимает `parent_trace_id` для интеграции с трассировкой LangSmith.
- **Логирование**: Все вызовы think-инструмента записываются в `ThinkLogEntry` и прикрепляются к финальному ответу под `metadata.thinkTool`.

### Трассировка LangSmith

- **Активация**: Включается через `LANGSMITH_TRACING=1` или передачей `metadata.langsmith` с `parent_run_id`, `trace_id` или `enabled=true` в вызове инструмента.
- **Жизненный цикл**: `LangSmithTracer.start()` создаёт run; `finalize_success()` или `finalize_error()` обновляют его с outputs/errors.
- **Сериализация метаданных**: Поле метаданных `langsmith` сериализуется в JSON-строку перед отправкой в OpenAI и десериализуется при возврате (см. `serialise_metadata_for_openai` / `deserialise_metadata_from_openai` в `app/utils/metadata.py`).
- **Прикрепление к ответу**: Run ID, trace ID и информация о проекте прикрепляются к `metadata.langsmith` в ответе инструмента.

## Соглашения о кодировании

- **Python 3.12 + FastAPI**, 4-пробельные отступы, порядок импортов PEP 8, полные аннотации типов.
- **Именование**: `snake_case` для функций/переменных, `UPPER_SNAKE_CASE` для констант, Pydantic-модели используют `CamelCase`.
- **Регистрация инструментов**: Новые инструменты должны быть добавлены в словарь `TOOLS` в `app/tools/registry.py` и их обработчики в `TOOL_HANDLERS` в `app/main.py`.

## Соглашения о тестировании

- **Расположение**: Добавляйте тесты в `tests/test_mcp_router.py`.
- **Именование**: `test_<behavior>` (например, `test_initialize_list_and_chat`).
- **Фикстуры**: Используйте autouse-фикстуру `clear_sessions` для сброса `ACTIVE_SESSIONS`.
- **Мокирование**: Мокайте клиент OpenAI через monkeypatching `app.main._create_openai_client` с `DummyOpenAIClient`, возвращающим объекты `DummyResponse`.
- **Утверждения**: Валидируйте структуру JSON-RPC, поля метаданных MCP (`toolCalls`, `isError`, `content`) и коды статусов ответов.

## Важные настройки конфигурации

- **Управление сессиями**: `MCP_REQUIRE_SESSION=false` разрешает автоматическое создание сессий для тестирования. В продакшене установите `true`.
- **Поллинг ответов**: Настройте `POLL_DELAY` (секунды между опросами) и `MAX_POLLS` (макс. попыток) через окружение.
- **Параллелизм**: `RESPONSES_POLL_MAX_CONCURRENCY` управляет семафором для параллельного поллинга (по умолчанию 8).
- **Think-инструмент**: `THINK_TOOL_ENABLED`, `THINK_TOOL_URL`, `THINK_TOOL_TIMEOUT_MS`, `THINK_TOOL_RETRY_LIMIT`.
- **LangSmith**: `LANGSMITH_TRACING=1`, `LANGSMITH_PROJECT=<name>` (опционально), `LANGSMITH_API_KEY` (из langsmith SDK).

## Примечания о структуре файлов

- **`deploy/`**: Шаблоны ingress и файлы окружения для продакшен-развёртывания.
- **`app/`**: Весь код приложения.
- **`tests/`**: pytest-сценарии.
- **`requirements.txt`**: Python-зависимости с ограничениями версий.
- **`docker-compose*.yaml`**: Конфигурации локального и продакшен Docker-стека.
- **`.env`**: Переменные окружения (не коммитится; см. `.env.example`, если присутствует).
- **`.dockerignore`**: Файлы, исключённые из контекста сборки Docker.

## Рабочий процесс разработки

1. **Начинайте с MVP**: Согласно `AGENTS.md`, начинайте с базового решения без преждевременных оптимизаций.
2. **Файлы задач**: Создавайте файлы `[task_name].md` для конкретных задач (реализация, анализ), включая постановку проблемы, подход и чеклист. Не создавайте файлы задач для простых вопросов или запросов документации к коммиту.
3. **Формат коммита**: `<full_branch_name>.<title_in_english>` с описанием на русском языке. См. `AGENTS.md` для деталей.
4. **Тестирование**: Запускайте pytest после изменений, чтобы убедиться в отсутствии регрессий. Мокайте внешние зависимости.
