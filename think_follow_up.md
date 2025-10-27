# Think-tool: корректная передача результатов в Responses API

## Описание задачи
- Responses API возвращает `function_call` для инструмента `think`.
- Наш backend должен выполнить локальный `think_tool` и отправить результат в OpenAI через повторный `responses.create` с `previous_response_id`.
- Ранее мы пытались использовать `responses.submit_tool_outputs`, из-за чего возникала ошибка «No tool output found for function call …».

## Подход
- Проверить текущую реализацию follow-up в `app/main.py`: для каждого `tool_call` типа `think` собираем `function_call_output` с корректным `call_id` и содержимым.
- Убедиться, что вывод `think_tool` нормализуется в строку (через объединение текстовых блоков) и упаковывается в блок `{"type": "input_text", "text": ...}` внутри массива `output`.
- Дополнить тесты (минимальный сценарий в `tests/test_mcp_router.py`), который эмулирует `function_call` и проверяет формирование follow-up.
- Обновить документацию/диагностику по необходимости, чтобы процесс был прозрачен для ноутбука и XAIA-бота.

## Чек-лист
- [x] Проанализировать поток `function_call` → `function_call_output` и закрепить `call_id`.
- [x] Нормализовать вывод `think_tool` в формат текстового блока Responses API.
- [x] Написать интеграционный тест для сценария с `think` и `function_call_output`.
- [ ] Прогнать ручной сценарий (ноутбук / XAIA-бот) после внесения правок.
