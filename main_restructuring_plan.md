# Реструктуризация `app/main.py`

## Описание задачи

Необходимо переразбить монолитный модуль `app/main.py` на логически обособленные пакеты, сохранив текущее поведение MCP-роутера и совместимость существующих тестов.

## Предлагаемый подход

- Сначала зафиксировать целевую архитектуру модулей, оставив `app/main.py` лишь точкой входа FastAPI.
- Постепенно переносить группы функций в новые файлы: модели, конфигурацию, инструменты, сервисы и маршруты.
- После каждого переноса выполнять ручные проверки целостности (pytest, дымовые запросы) для раннего обнаружения регрессий.

## Чек-лист пауз

- [x] Сформировать каркас каталогов и файлов:  
  `app/api/routes.py`, `app/core/config.py`, `app/core/session.py`, `app/models/json_rpc.py`, `app/services/openai_responses.py`, `app/tools/registry.py`, `app/tools/handlers.py`, добавить в подпапки соответствующие `__init__.py`, обновлённый `app/__init__.py`.
- [x] Пауза на пользовательскую проверку: `python -m pytest -k health` и локальный запуск `uvicorn app.main:app --port 8080` с GET `/health`.
- [x] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [x] Перенести JSON-RPC и сессионные модели в `app/models/json_rpc.py`, обновить импорты.
- [x] Пауза на пользовательскую проверку: `python -m pytest tests/test_mcp_router.py::test_initialize`.
- [x] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [x] Вынести константы, фичефлаги и глобальные объекты (`PROTOCOL_VERSION`, `SERVER_CAPABILITIES`, `ACTIVE_SESSIONS`, конфиг think-tool) в `app/core/config.py` и `app/core/session.py`.
- [x] Пауза на пользовательскую проверку: `python -m pytest tests/test_mcp_router.py::test_tools_list`.
- [x] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [x] Переместить описание схем, спецификаций инструментов и словарь `TOOLS` в `app/tools/registry.py`; обработчики (`_handle_echo`, `_handle_read_file`, `_handle_think`) в `app/tools/handlers.py`.
- [x] Пауза на пользовательскую проверку: `python -m pytest tests/test_mcp_router.py::test_tools_call`.
- [x] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [x] Вынести функции интеграции с OpenAI/Responses API и вспомогательные нормализации в `app/services/openai_responses.py`, обеспечить доступ к think-tool клиенту через зависимости.
- [ ] Пауза на пользовательскую проверку: полный `python -m pytest` и ручной smoke-тест hosted tools (если доступно).
- [ ] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [ ] Создать `app/api/routes.py`, где разместить обработчики `initialize`, `tools/list`, `tools/call`, `health`; в `app/main.py` оставить только инициализацию приложения и импорт маршрутов.
- [ ] Пауза на пользовательскую проверку: локальный прогон `uvicorn` + `pytest`, убедиться в отсутствии regresison в логах.
- [ ] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
- [ ] Актуализировать документацию (README/докстринги) под новую структуру.
- [ ] Пауза на пользовательскую проверку: визуальный аудит структуры, `python -m compileall app`.
- [ ] Пауза на пользовательскую проверку: `jupyter-notebook` `/Users/romankassymov/python_web/jupyter-diagnostic/notebooks/mcp-openai-router/mor-python.ipynb`, telegramm bot Xaia..
