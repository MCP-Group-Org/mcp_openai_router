# MCP OpenAI Router

**MCP OpenAI Router** — это минимальная реализация MCP-сервера, проксирующего запросы к моделям OpenAI.  
Он предоставляет единый интерфейс MCP, позволяющий использовать модели OpenAI (например, `gpt-4.1-mini`) без прямого обращения к их API.

## Возможности

- Поддержка любых моделей OpenAI (например, `gpt-4.1-mini`, `gpt-4o-mini`, `gpt-3.5-turbo`).
- Простая настройка через переменные окружения.
- Запуск в составе Docker-сети для интеграции с другими MCP-сервисами.
- Healthcheck для диагностики состояния контейнера.

## Архитектура

```
MCP Client ⇄ MCP OpenAI Router ⇄ OpenAI API ⇄ Модель
```

Роутер **не содержит моделей внутри себя**, а лишь пересылает MCP-запросы к OpenAI.

## Требования

- Docker и Docker Compose
- Действующий [OpenAI API Key](https://platform.openai.com/)

## Установка и запуск

1. Клонировать репозиторий:
   ```bash
   git clone https://github.com/MCP-Group-Org/mcp_openai_router.git
   cd mcp_openai_router
   ```

2. Создать файл `.env`:
   ```env
   OPENAI_API_KEY=sk-************************
   MODEL=gpt-4.1-mini
   ```

3. Запустить контейнер:
   ```bash
   docker compose up -d --build
   ```

4. Проверить работу контейнера:
   ```bash
   curl http://localhost:8001/health
   ```

   > Порт `8001` проброшен наружу. Внутри контейнера сервер слушает порт `8000`.

## Конфигурация

- `OPENAI_API_KEY` — ключ доступа к OpenAI API (обязательный параметр).
- `MODEL` — имя модели (например, `gpt-4.1-mini`).
- `PORT` — внутренний порт (по умолчанию `8000`).

## Использование

После запуска роутер становится доступен как **MCP-сервис**.  
Для работы используйте клиент, совместимый с MCP (например, LangGraph, LangChain MCP-интеграции или собственный MCP-клиент).  

Напрямую через `curl` запросы к моделям не выполняются — это часть MCP-протокола.

## Проверка chat-инструмента

1. Убедитесь, что контейнер запущен и доступен (`docker compose up -d --build`) и заданы переменные окружения `OPENAI_API_KEY` и `MODEL`.
2. Проверьте диагностику: `curl http://localhost:8001/diagnostics` — в блоке `tools.names` должно быть имя `chat`, а `openai.sdk_available` — `true`.
3. Выполните тестовый JSON-RPC запрос с коротким вопросом и требованием краткого ответа:
   ```bash
   curl -s http://localhost:8001/mcp \
     -H 'Content-Type: application/json' \
     -d '{
       "jsonrpc": "2.0",
       "id": "chat-test",
       "method": "tools/call",
       "params": {
         "name": "chat",
         "arguments": {
           "model": "gpt-4.1-mini",
           "messages": [
             {"role": "system", "content": "Ответь очень кратко."},
             {"role": "user", "content": "ping"}
           ]
         }
       }
     }'
   ```
4. В ответе поле `result.message.content` должно содержать короткое подтверждение (например, `pong`). В случае ошибки проверьте `error.data` и логи (`docker compose logs -f`).

## Возможные доработки

- Динамический выбор моделей.
- Логирование MCP-запросов и ответов.
- Кэширование и лимитирование запросов.
- Интеграция с менеджерами секретов.
- Набор автотестов и CI/CD pipeline.

## Лицензия
