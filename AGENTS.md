# Repository Guidelines

Основные инструкции для участников: всегда отвечайте и поясняйте на русском языке, начинайте с базового решения без избыточных оптимизаций и для каждой новой задачи создавайте файл `[name_task].md` с описанием, подходом и чек-листом ([]/[x]) с точками остановки для тестирования, анализа или коммитов.

## User Instructions

- Always respond and provide clarifications in Russian when collaborating on this repository.
- Deliver each assigned task at the minimal viable level first, postponing advanced tooling or optimizations until the base solution is verified.
- For every new task, create an instructions file `[name_task].md` that includes the problem statement, proposed solution approach, and a checkbox plan ([]/[x]) with pause points for testing, analysis, or commits.

### Перевод (справочно: User Instructions)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Всегда отвечайте и поясняйте на русском языке при работе с репозиторием.
- Выполняйте каждую задачу на минимально базовом уровне, откладывая дополнительные инструменты и оптимизации до подтверждения базового решения.
- При постановке новой задачи создавайте файл инструкций `[name_task].md` с описанием задачи, подходом к решению и чек-листом ([]/[x]) с точками остановки для тестов, анализа или коммитов.

## Project Structure & Module Organization

- `app/main.py` exposes the FastAPI service that implements the MCP JSON-RPC endpoints, tool registry, and OpenAI routing logic. Treat it as the authoritative place for session handling and tool adapters.
- `tests/test_mcp_router.py` contains the pytest suite that exercises the full handshake (`initialize → tools/list → tools/call`). Use it as a template when adding new endpoints or tools.
- `deploy/` stores Caddy ingress templates, while `docker-compose.yaml` and `docker-compose.local.yaml` describe production and local stacks. Keep new assets in these directories to avoid leaking into the runtime image.

### Перевод (справочно: Project Structure & Module Organization)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- `app/main.py` публикует сервис FastAPI с реализацией MCP JSON-RPC, реестром инструментов и логикой маршрутизации OpenAI. Рассматривайте его как главный источник для работы с сессиями и адаптерами инструментов.
- `tests/test_mcp_router.py` содержит набор pytest, покрывающий полный обмен (`initialize → tools/list → tools/call`). Используйте его как шаблон при добавлении новых эндпоинтов или инструментов.
- `deploy/` хранит шаблоны Caddy, а `docker-compose.yaml` и `docker-compose.local.yaml` описывают продовую и локальную конфигурации. Раскладывайте новые артефакты по этим каталогам, чтобы не загрязнять образ выполнения.

## Build, Test, and Development Commands

- Install dev dependencies with `pip install fastapi-mcp "mcp[cli]" pytest httpx` (match the versions in the Dockerfile).
- Run the API locally via `uvicorn app.main:app --reload --port 8080`; export `OPENAI_API_KEY` and optional `MCP_REQUIRE_SESSION=false` for unauthenticated testing.
- Execute the suite using `python -m pytest`. CI expects the chat tool test to pass without live OpenAI calls, so keep the dummy client pattern.
- For container parity, use `docker compose -f docker-compose.local.yaml up --build` and rely on the mounted `/app` volume for hot reloads.

### Перевод (справочно: Build, Test, and Development Commands)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Устанавливайте дев-зависимости командой `pip install fastapi-mcp "mcp[cli]" pytest httpx`, соблюдая версии из Dockerfile.
- Локально запускайте API через `uvicorn app.main:app --reload --port 8080`; перед тестами экспортируйте `OPENAI_API_KEY` и при необходимости `MCP_REQUIRE_SESSION=false` для неавторизованных вызовов.
- Прогоняйте тесты командой `python -m pytest`. CI ожидает прохождения проверки chat-инструмента без живых запросов в OpenAI, поэтому сохраняйте шаблон подмены клиента.
- Для соответствия контейнерной среде используйте `docker compose -f docker-compose.local.yaml up --build` и опирайтесь на примонтированный каталог `/app` для горячей перезагрузки.

## Coding Style & Naming Conventions

- Follow Python 3.12 + FastAPI conventions: 4-space indentation, PEP 8 imports, and full type hints (match the existing `ToolSpec` and Pydantic models).
- Tool and handler names stay in `snake_case`; public constants such as `PROTOCOL_VERSION` remain `UPPER_SNAKE_CASE`.
- When adding new tools, define schemas through `ToolSchema`/`ToolSpec` and expose handlers via `_register_tool_*` helpers to keep the registry predictable.

### Перевод (справочно: Coding Style & Naming Conventions)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Соблюдайте соглашения Python 3.12 + FastAPI: отступы в 4 пробела, импорты по PEP 8 и полные типизации (по аналогии с `ToolSpec` и моделями Pydantic).
- Названия инструментов и обработчиков оставляйте в `snake_case`; публичные константы вроде `PROTOCOL_VERSION` оформляйте в `UPPER_SNAKE_CASE`.
- При добавлении новых инструментов описывайте схемы через `ToolSchema`/`ToolSpec` и регистрируйте обработчики через хелперы `_register_tool_*`, чтобы реестр оставался предсказуемым.

## Testing Guidelines

- Extend `tests/test_mcp_router.py` with scenario-focused functions named `test_<behavior>`; prefer fixtures to manage globals like `ACTIVE_SESSIONS`.
- Mock OpenAI clients by monkeypatching `_create_openai_client` so tests stay hermetic and offline.
- Gate new features behind pytest assertions that verify both JSON-RPC payloads and MCP metadata (e.g., `toolCalls`, `isError`).

### Перевод (справочно: Testing Guidelines)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Расширяйте `tests/test_mcp_router.py` функциями под конкретные сценарии с именованием `test_<поведение>`; для работы с глобалами вроде `ACTIVE_SESSIONS` используйте фикстуры.
- Эмулируйте клиентов OpenAI через monkeypatch `_create_openai_client`, чтобы тесты оставались герметичными и офлайн.
- Подкрепляйте новые возможности pytest-ассерциями, проверяющими JSON-RPC полезные данные и MCP-метаданные (например, `toolCalls`, `isError`).

## Commit & Pull Request Guidelines

- Use conventional commit prefixes seen in history (`feat:`, `fix:`, `chore:`) and concise imperatives. Align branch names with `feature/<issue-id>` where possible.
- PRs should summarize behavioral changes, list impacted tools or endpoints, and note how tests were run (`pytest`, Docker scenario, etc.).
- Link tracking issues or roadmap items and add screenshots or JSON samples only when they clarify API changes.

### Перевод (справочно: Commit & Pull Request Guidelines)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Используйте префиксы conventional commits из истории (`feat:`, `fix:`, `chore:`) и краткие повелительные формулировки. По возможности приводите названия веток к виду `feature/<issue-id>`.
- В PR описывайте поведенческие изменения, перечисляйте затронутые инструменты или эндпоинты и фиксируйте способ прогона тестов (`pytest`, сценарий Docker и т.д.).
- Привязывайте связанные задачи или пункты дорожной карты и добавляйте скриншоты либо JSON-примеры только там, где они проясняют изменения API.

## Security & Configuration Tips

- Load secrets via `.env` (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MCP_REQUIRE_SESSION`). Never commit real keys; use placeholders in examples.
- Keep `MCP_ENABLE_LEGACY` toggles off in production unless you must support the deprecated `tools.*` endpoints; document the fallback path when enabling it.
- Validate new file-reading surfaces against the `/app` root to preserve the sandbox guarantees provided by the `read_file` tool.

### Перевод (справочно: Security & Configuration Tips)
>
> _Адаптация на русском языке для удобства чтения; основными остаются инструкции выше._

- Загружайте секреты через `.env` (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MCP_REQUIRE_SESSION`). Никогда не коммитьте реальные ключи, используйте заглушки в примерах.
- Держите флаг `MCP_ENABLE_LEGACY` выключенным в проде, если только не требуется поддержка устаревших эндпоинтов `tools.*`; при включении фиксируйте путь отката.
- Проверяйте новые возможности чтения файлов относительно корня `/app`, чтобы сохранять гарантию песочницы, которую обеспечивает инструмент `read_file`.
