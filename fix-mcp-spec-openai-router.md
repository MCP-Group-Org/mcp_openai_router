# План приведения mcp-openai-router к MCP-спецификации

## Цель
- router должен проходить MCP-handshake (`initialize`) и поддерживать `tools/list`, `tools/call` с методом `chat`.
- убираем нестандартные JSON-RPC алиасы (`tools.call`) и устаревшие методы (`tools.*`).
- LLM-инструмент `chat` обязан возвращать `tool_calls`, если модель их сгенерировала.

## 1. Анализ текущей реализации
- [x] Проверить FastAPI-код (`app/main.py`) на соответствие MCP handshake:
  - `/mcp` сейчас GET, но возвращает кастомный payload (нет `protocolVersion`, `capabilities`).
  - Нет обработчика `initialize`; handshake MCP подразумевает `POST /mcp` с `initialize`.
- [x] `JsonRpcRequest`/`Response` — минимальные, но нет поддержки `initialize`, `notifications`, `session`.
- [x] `TOOLS` содержит `chat`, `echo`, `read_file`:
  - `chat` возвращает `message` строкой, без массива content-частей и без `tool_calls`.
  - Не обрабатываются входящие `tool_calls` от модели (если Responses API вернёт hosting tool).
- [x] Наличие legacy-методов (`tools.echo`, `tools.read_file`), что не соответствует MCP.

## 2. Требования MCP (контрольный список)
- [x] `/mcp` должен отвечать на `POST` с методами `initialize`, `shutdown`, `ping` (см. спецификацию).
- [x] `tools/list` возвращает массив `Tool` c полями `inputSchema`/`outputSchema`.
- [x] `tools/call` принимает `name` и `arguments` и возвращает `content` (список блоков) + `isError`.
- [x] `chat` как tool должен формировать результат в формате:
  ```json
  {
    "content": [{"type": "text", "text": "..."}],
    "toolCalls": [ ... ],
    "isError": false
  }
  ```
- [x] Поддержка hosted tools: если OpenAI вернул `tool_calls`, их нужно транслировать в MCP-формат.
- [x] В логике Responses API учитывать `parallelToolCalls` и правильное завершение (`finish_reason`).
- [x] Ошибки возвращать через `error` структуры MCP, а не JSON-RPC уровня.
- [x] Подумать о session management (`initialize` -> sessionId) и stateful режиме.

## 3. План доработки
1. **Handshake**:
   - [x] Добавить `initialize` обработчик (`tools/list`/`resources/list` пустые или согласно roadmap).
   - [x] Возвращать `InitializeResult` с `protocolVersion` и поддерживаемыми возможностями.
   - [x] Поддержать `shutdown`, `ping` по специфике MCP.
2. **Интеграция tools API**:
   - [x] Переписать ответ `tools/call` -> возвращать `ToolResponse` (с `content`, `isError`, `toolCalls`).
   - [x] Конвертировать OpenAI ответы (Responses API) в массив content-блоков (`text`, `input_text`).
   - [x] Если Responses API вернул tool calls, пробрасывать их (формат MCP `ToolCall`).
   - [x] Добавить логирование/обработку ошибок (HTTP status, JSON)
3. **Удалить/изолировать legacy RPC**:
   - [x] Временно поддержать, но повесить флаг `--legacy` + TODO на удаление.
   - [x] Обновить README: `support MCP tool calling`.
4. **Тесты и CI**:
   - [x] Написать интеграционный тест (pytest + httpx): `initialize -> tools/list -> tools/call(chat)`.
   - [x] Проверить Responses API path и hosted tools.
   - [ ] Линтеры/ruff.
5. **Документация**:
   - [x] README.md: раздел «Compatibility» + пример MCP клиента.
   - [x] Сценарии с hosted tools, примеры.

## 4. Риски/вопросы
- [x] session (`initialize` возвращает `sessionId`) — нужно поддерживать stateful режим.
- [x] Делаем create_and_poll
- [ ] ~~Авторизация: сейчас API ключ передаётся напрямую; подумать о безопасной передаче/secret management.~~ Этот контейнер пока будет использоваться в локальном докер окружении (сети), авторизация не нужна - убрать.

---
**TODO:** после реализации повторно включить think-tool в `node_llm_answer`.
