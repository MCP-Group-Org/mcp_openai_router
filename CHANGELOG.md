# Changelog

## Unreleased

- Рефакторили структуру приложения: вынесли JSON-RPC маршруты в `app/api/routes.py`, переработали `app/main.py` и обновили README под новую архитектуру.
- Продолжили декомпозицию `_handle_chat`: добавили `OpenAIClientAdapter`, `ResponsePoller`, вынесли think-цикл в `ThinkToolProcessor`, оформили результат в дата-классы (`ProcessingResult`, `ThinkProcessResult`) и расширили тестовое покрытие (`tests/test_mcp_router.py`).

## v0.0.2 (2025-10-19)

- Добавил поддержку codex и файл AGENTS.md
- Привели конфигурацию запуска к обязательному использованию переменной `PORT`, убрали запасные значения по умолчанию.
- Добавили поддержку сети `mcp-net` и алиаса `MCP_OPENAI_ROUTER_ENDPOINT` в `docker-compose*` для однозначного разрешения имени контейнера.
- Дополнили обработчик `chat` поддержкой проксирования `tools` и `tool_choice`, ввели флаг `MCP_REQUIRE_SESSION` для отключения жёсткой проверки сессий.
- Обновили ноутбук проверки (mcp_agent_py), добавив сценарий вызова инструмента `chat` и поясняющие markdown-блоки.
