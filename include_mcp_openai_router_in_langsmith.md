# Задача

- Обеспечить передачу `metadata.langsmith` в запросы `mcp_agent_py` к `mcp-openai-router`, чтобы трассы роутера появлялись внутри графа LangSmith агента.

## Подход

- На этапе формирования payload для `router_client.call_tool("chat", ...)` дополнить `router_payload["metadata"]`.
- Берём активные идентификаторы LangSmith (parent_run_id, trace_id) из текущего контекста трассировки (`safe_trace` / `safe_tracing_context`).
- Прикладываем проект, теги и дополнительные метаданные для диагностических целей.

## Требуемая структура блока

```json
"metadata": {
  "langsmith": {
    "enabled": true,
    "parent_run_id": "<parent-run>",
    "trace_id": "<trace-id>",
    "run_id": "<optional-existing-child-run>",
    "project": "<settings.langsmith_project>",
    "tags": ["mcp", "router"],
    "metadata": {
      "app_env": "<settings.app_env>",
      "endpoint": "<router_endpoint>"
    }
  }
}
```

- Минимальный набор: `enabled`, `parent_run_id` **или** `trace_id` (любой из двух связывает спаны).
- `metadata` вложенный разумно использовать для контекстной информации.
- Для follow-up запросов (когда Responses API возвращает `previous_response_id`) переиспользуем тот же блок.

## Изменения в коде

1. `src/mcp_agent/agent/graph.py`
   - Перед `router_client.call_tool("chat", router_payload)` вставить формирования блока `langsmith`.
   - Пример: 
     ```python
     from mcp_agent.trace_safety import current_run_ids  # понадобится вспомогательная функция

     parent_run_id, trace_id = current_run_ids()
     if parent_run_id or trace_id:
         router_metadata = router_payload.setdefault("metadata", {})
         router_metadata["langsmith"] = {
             "enabled": True,
             "parent_run_id": parent_run_id,
             "trace_id": trace_id,
             "project": settings.langsmith_project,
             "tags": ["mcp", "router"],
             "metadata": {
                 "app_env": settings.app_env,
                 "endpoint": router_endpoint,
             },
         }
     ```
   - В follow-up блоке (когда отправляем `previous_response_id`) переиспользовать `router_metadata`.

2. При необходимости добавить в `trace_safety.py` утилиту получения текущих run-id (из `tracing_context` или `langsmith.env.get_run_tree()`).

## Чеклист

- [ ] Получить текущие идентификаторы LangSmith в узле `node_mcp_openrouter` и `node_llm_answer`.
- [ ] Инжектировать `metadata.langsmith` в первый и последующие вызовы `router_client.call_tool`.
- [ ] Убедиться, что при отсутствующем контексте (ручные вызовы) код не падает.
- [ ] Проверить трассу LangSmith: дочерний спан `mcp-openai-router.chat` отображается под `mcp_tools_call`.
