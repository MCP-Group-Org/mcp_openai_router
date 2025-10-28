# Repository Guidelines

Основные инструкции для участников: всегда отвечайте и поясняйте на русском языке, начинайте с базового решения без избыточных оптимизаций и для каждой новой задачи создавайте файл `[name_task].md` с описанием, подходом и чек-листом ([]/[x]) с точками остановки для тестирования, анализа или коммитов.

## User Instructions

- Always respond and provide clarifications in Russian when collaborating on this repository.
- Deliver each assigned task at the minimal viable level first, postponing advanced tooling or optimizations until the base solution is verified.
- For every new task, create an instructions file `[name_task].md` that includes the problem statement, proposed solution approach, and a checkbox plan ([]/[x]) with pause points for testing, analysis, or commits.

## Project Structure & Module Organization

- `app/main.py` exposes the FastAPI service that implements the MCP JSON-RPC endpoints, tool registry, and OpenAI routing logic. Treat it as the authoritative place for session handling and tool adapters.
- `tests/test_mcp_router.py` contains the pytest suite that exercises the full handshake (`initialize → tools/list → tools/call`). Use it as a template when adding new endpoints or tools.
- `deploy/` stores Caddy ingress templates, while `docker-compose.yaml` and `docker-compose.local.yaml` describe production and local stacks. Keep new assets in these directories to avoid leaking into the runtime image.

## Build, Test, and Development Commands

- Install dev dependencies with `pip install fastapi-mcp "mcp[cli]" pytest httpx` (match the versions in the Dockerfile).
- Run the API locally via `uvicorn app.main:app --reload --port 8080`; export `OPENAI_API_KEY` and optional `MCP_REQUIRE_SESSION=false` for unauthenticated testing.
- Execute the suite using `python -m pytest`. CI expects the chat tool test to pass without live OpenAI calls, so keep the dummy client pattern.
- For container parity, use `docker compose -f docker-compose.local.yaml up --build` and rely on the mounted `/app` volume for hot reloads.

## Coding Style & Naming Conventions

- Follow Python 3.12 + FastAPI conventions: 4-space indentation, PEP 8 imports, and full type hints (match the existing `ToolSpec` and Pydantic models).
- Tool and handler names stay in `snake_case`; public constants such as `PROTOCOL_VERSION` remain `UPPER_SNAKE_CASE`.
- When adding new tools, define schemas through `ToolSchema`/`ToolSpec` and expose handlers via `_register_tool_*` helpers to keep the registry predictable.

## Testing Guidelines

- Extend `tests/test_mcp_router.py` with scenario-focused functions named `test_<behavior>`; prefer fixtures to manage globals like `ACTIVE_SESSIONS`.
- Mock OpenAI clients by monkeypatching `_create_openai_client` so tests stay hermetic and offline.
- Gate new features behind pytest assertions that verify both JSON-RPC payloads and MCP metadata (e.g., `toolCalls`, `isError`).

## Commit & Pull Request Guidelines

- Use conventional commit prefixes seen in history (`feat:`, `fix:`, `chore:`) and concise imperatives. Align branch names with `feature/<issue-id>` where possible.
- PRs should summarize behavioral changes, list impacted tools or endpoints, and note how tests were run (`pytest`, Docker scenario, etc.).
- Link tracking issues or roadmap items and add screenshots or JSON samples only when they clarify API changes.

## Security & Configuration Tips

- Load secrets via `.env` (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MCP_REQUIRE_SESSION`). Never commit real keys; use placeholders in examples.
- Keep `MCP_ENABLE_LEGACY` toggles off in production unless you must support the deprecated `tools.*` endpoints; document the fallback path when enabling it.
- Validate new file-reading surfaces against the `/app` root to preserve the sandbox guarantees provided by the `read_file` tool.

## Commit Documentation Rules

> Commit messages should follow the format:  
> [`full_branch_name`].[`title` (eng)] \r [`description` (ru/rus)]
>

Where:

- The documentation is prepared without the usually required `.md` plan file.
- `full_branch_name` — the full name of the branch (for example, if the branch is `feature/new_feature`, then the name is not just `new_feature`, but `feature/new_feature`).
- `title` — a short summary in English, consisting of 3–5–7 words.
- `description` — a more detailed list of the changes or additions made, written in Russian.
- Commit documentation should be prepared with respect to the files in the index, not the already committed changes.
- The resulting content should be inserted into the `./.git/COMMIT_EDITMSG` file.
