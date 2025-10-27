# Протокол Responses API при использовании инструментов

## Основные сущности и требования

- **Первый запрос (`responses.create`)**. Клиент передаёт модель, входные сообщения и список инструментов (`tools`). Модель может вернуть блок `tool_call` / `function_call` с полем `id` — его важно сохранить.
- **Локальное выполнение инструментов**. По каждому `tool_call` сервер/клиент выполняет соответствующий инструмент и формирует результат. Для Responses API ожидаемый формат результата — объект `{"type": "function_call_output", "call_id": "<id из tool_call>", "output": [{"type": "input_text", "text": "<строковый результат>"}]}`.
- **Повторный запрос (`responses.create`)**. Чтобы передать результаты технологии, вызывается `responses.create` с параметром `previous_response_id` и массивом `input`, содержащим объекты `function_call_output`. Поле `output` — массив контент-блоков (для текстового результата обычно один блок `{"type": "input_text", "text": "..."}`).
- **Дополнительные сообщения**. В `input` можно добавлять и обычные сообщения (например, `developer` с подсказкой), но ключевой сигнал для модели — наличие `function_call_output`.
- **Финальный ответ**. OpenAI возвращает завершённый ответ (обычно блок `message` с текстом). Если модель решит повторно вызвать инструмент, цикл повторяется.

## Пример обмена

### 1. Первый запрос
```json
POST /responses
{
  "model": "gpt-4.1-mini",
  "input": [
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "Пожалуйста, запусти think-tool и верни результат."}
      ]
    }
  ],
  "tools": [
    {
      "type": "function",
      "name": "think",
      "description": "Сохраняет промежуточные размышления пользователя.",
      "parameters": {
        "type": "object",
        "properties": {
          "thought": {"type": "string"},
          "parent_trace_id": {"type": "string"}
        },
        "required": ["thought"],
        "additionalProperties": false
      }
    }
  ],
  "tool_choice": "auto"
}
```

### 2. Ответ OpenAI c `function_call`
```json
{
  "id": "resp_abc123",
  "status": "in_progress",
  "output": [
    {
      "type": "function_call",
      "id": "call_xyz789",
      "name": "think",
      "arguments": "{\"thought\": \"Контейнеров в сети mcp-net: ???\"}"
    }
  ]
}
```

### 3. Локальное исполнение
```json
// backend вызывает think_tool и получает результат
{
  "content": [
    {"type": "text", "text": "Контейнеров в сети mcp-net: 3"}
  ]
}
```

### 4. Повторный запрос (передача результата инструмента)
```json
POST /responses
{
  "model": "gpt-4.1-mini",
  "previous_response_id": "resp_abc123",
  "input": [
    {
      "type": "function_call_output",
      "call_id": "call_xyz789",
      "output": [
        {"type": "input_text", "text": "Контейнеров в сети mcp-net: 3"}
      ]
    },
    {
      "role": "developer",
      "content": [
        {"type": "input_text", "text": "Подготовь краткий итог ответа для пользователя."}
      ]
    }
  ]
}
```

### 5. Финальный ответ OpenAI
```json
{
  "status": "completed",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "Сеть mcp-net подключает 3 контейнера. Могу подсказать, какие именно?"}
      ]
    }
  ]
}
```

## Проверки на стороне сервера

- Убедиться, что для каждого `tool_call` из первого ответа отправлен `function_call_output` с тем же `call_id`.
- Гарантировать, что `output` содержит массив валидных контент-блоков (`input_text`, `input_image` и т.д.).
- Следить за циклом: если OpenAI вновь возвращает `function_call`, повторить шаги 3–4 до тех пор, пока не будет финального ответа или лимит итераций не исчерпан (в нашем случае 5).
*** End Patch

## Gaid

Коротко: почти всё верно. Ниже — где совпадает с официальными доками, а где нужны нюансы/поправки.

Что правильно
	•	Цикл tool calling. Первый responses.create может вернуть элементы function_call (а также другие tool-calls). Их call_id действительно нужно сохранить, выполнить инструмент локально и передать результат во втором responses.create как элементы {"type":"function_call_output","call_id": "...", ...} вместе с previous_response_id. Это ровно так показано в официальном примере Azure/OpenAI: проход по response.output, поиск output.type == "function_call", формирование function_call_output, затем второй вызов с previous_response_id.  ￼
	•	previous_response_id — корректный способ «сшивать» цепочку ответов. Он сохраняет контекст и (важно) позволяет не повторять объявление инструментов при следующем вызове. Это подчёркнуто и в общем описании чейнинга.  ￼
	•	Смешивание дополнительных сообщений. В следующий вызов можно добавить обычные сообщения (включая user, developer и т.п.). Наличие developer-сообщений для Responses подтверждается SDK/доками Azure (“ResponsesDeveloperMessage”).  ￼
	•	Финальный ответ. Если модель не запрашивает новые инструменты, вернётся обычный message c output_text; если запрашивает — цикл повторяется. Это также видно в примерах «Function calling».  ￼

Где нужны уточнения
	•	Формат output в function_call_output. В доках и примерах допустим простой строковый результат (часто так и делают: "output": "{\"temperature\":\"70 degrees\"}"). В более новых описаниях указывается, что output может быть строкой или списком контент-блоков (например, input_text, input_image и т.п.). Так что ваш вариант с массивом блоков валиден, но строка тоже официально поддерживается.  ￼
	•	tool_call vs function_call. В Responses API «инструментальные» элементы бывают разных типов: для функций — function_call, для других встроенных инструментов — свои (code_interpreter_call, image_generation_call, web_search_call, и т.д.). В тексте лучше говорить «tool-calls (например, function_call)», как вы фактически и сделали.  ￼
	•	Лимит итераций. Указание «лимит 5» — это прикладное ограничение (гардвейл оркестратора), а не требование API. В официальных гайдах ограничение числа циклов не навязывается (типичная ошибка при несоответствии — «No tool output found for function call», когда не вернули function_call_output для каждого вызванного call_id).  ￼

Итого по оценке
	•	Логика протокола (шаги 1–5) — верно.
	•	Структура function_call_output — верно, но дополните, что вместо массива блоков можно отправить одну строку (оба варианта поддержаны официально).
	•	Про «дополнительные сообщения» — верно.
	•	Про лимит итераций — это ваше внутреннее правило, не требование API.

Полезные ссылки на первоисточники:
	•	Пример function calling в Responses с function_call_output и previous_response_id.  ￼
	•	Гайд по чейнингу через previous_response_id.  ￼
	•	Наличие developer-сообщений в Responses (модель объектов).  ￼
	•	Замечание про форму output (строка или список блоков) в FunctionCallOutput.  ￼
	•	Типовая ошибка при пропуске function_call_output (“No tool output found for function call”).  ￼

Если хотите, могу приложить минимальный рабочий шаблон (Python/JS) с обоими вариантами output (строка и массив блоков).
