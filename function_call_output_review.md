# Как подружить OpenAI-модель с внешними инструментами в Python (SDK 2.6.x)

Когда что использовать
 • Responses API — «основной» вариант для агентных сценариев, многошаговых цепочек и встроенных хостовых тулов (web-search, file-search и т. п.). Умеет аккуратно продолжать ход через previous_response_id и принимать результаты ваших функций через специальные элементы function_call_output. Док: <https://platform.openai.com/docs/api-reference/responses> ; обзор в README SDK: <https://github.com/openai/openai-python> .  ￼
 • Chat Completions — классический function-calling: объявляете tools, модель возвращает tool_calls, вы исполняете и шлёте ответ с ролью tool. Гайд-ноутбук: <https://cookbook.openai.com/examples/how_to_call_functions_with_chat_models> .  ￼

⸻

Паттерн №1: Chat Completions + свои функции (минимум кода)

## pip install openai

import json
from openai import OpenAI

client = OpenAI()

tools = [{
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location", "unit"],
            "additionalProperties": False
        },
        "strict": True
    }
}]

messages = [
    {"role": "user", "content": "Weather in Berlin in celsius?"}
]

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=tools,
    tool_choice="auto"
)

msg = resp.choices[0].message
for call in (msg.tool_calls or []):
    args = json.loads(call.function.arguments or "{}")
    # тут ваш внешний вызов
    result = f"{args['location']}: 12° {args['unit']}"
    messages.append({
        "role": "tool",
        "tool_call_id": call.id,
        "content": result
    })

final = client.chat.completions.create(model="gpt-4o", messages=messages)
print(final.choices[0].message.content)

Подробно про tools, tool_choice, tool_calls и обратную отправку результата инструментов — в Cookbook: <https://cookbook.openai.com/examples/how_to_call_functions_with_chat_models> .  ￼

⸻

Паттерн №2: Responses API + итеративная оркестрация («loop-executor»)

Зачем нужен цикл. Модель может:
 • за один шаг вернуть несколько вызовов функций;
 • после вашего function_call_output запросить ещё данные;
 • комбинировать ваши функции с хостовыми тулзами (например, web-search).

Поэтому строим петлю: «вызвал → исполнил → вернул результаты → повторил, пока не придёт финальное сообщение». Это рекомендуемый подход в Cookbook по Responses API. Примеры многошаговой оркестрации и RAG: <https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼

Мини-шаблон loop-executor

import json
from typing import Callable, Dict, Any, List
from openai import OpenAI

client = OpenAI()

## Ваши реальные функции

TOOL_MAP: Dict[str, Callable[..., Any]] = {
    "get_user_info": lambda user_id: {"id": user_id, "score": 87},
    "calculate_score": lambda base: {"score": base["score"] * 1.1},
}

TOOLS = [{
    "type": "function",
    "name": "get_user_info",
    "description": "Fetch user profile by id",
    "parameters": {
        "type": "object",
        "properties": {"user_id": {"type": "string"}},
        "required": ["user_id"],
        "additionalProperties": False
    },
    "strict": True
}, {
    "type": "function",
    "name": "calculate_score",
    "description": "Recalculate score given base profile",
    "parameters": {
        "type": "object",
        "properties": {"base": {"type": "object"}},
        "required": ["base"],
        "additionalProperties": False
    },
    "strict": True
}]

def run_with_tools(prompt: str, model: str = "gpt-4o", max_steps: int = 7):
    prev_id = None
    payload: List[dict] = [{"role": "user", "content": prompt}]

    for _ in range(max_steps):
        resp = client.responses.create(
            model=model,
            input=payload,
            tools=TOOLS,
            previous_response_id=prev_id  # «сшивает» ход на стороне API
        )

        calls = [x for x in resp.output if getattr(x, "type", "") == "function_call"]
        if not calls:
            return resp.output_text  # финальный ответ

        next_items = []
        for call in calls:
            args = json.loads(call.arguments or "{}")
            try:
                result = TOOL_MAP[call.name](**args)
            except Exception as e:
                result = {"error": str(e)}
            next_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result
            })

        payload = next_items
        prev_id = resp.id

    raise RuntimeError("Max tool-calling steps exceeded")

Пример function_call_output, сопоставление call_id и многошаговый цикл с previous_response_id — в Cookbook:
<https://cookbook.openai.com/examples/reasoning_function_calls>
<https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼

⸻

Несколько вызовов за один шаг и (возможная) параллельность
 • Обрабатывайте список function_call в response.output.
 • В некоторых кейсах можно включить parallel_tool_calls=True при создании ответа — тогда модель может планировать независимые вызовы. Пример с parallel_tool_calls в Cookbook: <https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼

⸻

Хостовые (встроенные) инструменты vs. свои
 • Хостовые в Responses API (например, web_search, file_search) подключаются прямо параметром tools=[{"type": "..."}] и отлично сочетаются с вашими функциями в одном цикле. См. пошаговую оркестрацию с web-search: <https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼
 • Свои функции описывайте JSON-схемой и возвращайте вывод через {"type": "function_call_output", "call_id": ..., "output": ...} — пример кода и формата: <https://cookbook.openai.com/examples/reasoning_function_calls> .  ￼

⸻

Надёжность на проде: защита от «вечного» цикла и сетевых сбоев

 1. Лимит шагов в лупе (например, 5–7) и явный выход с ошибкой, если превысили.
 2. Таймауты и ретраи (экспоненциальная пауза) на сетевые вызовы и на ваши инструменты. В Cookbook часто используют tenacity именно для таких ретраев: <https://cookbook.openai.com/examples/how_to_call_functions_with_chat_models> .  ￼
 3. Строгая схема аргументов (additionalProperties: false, enum, required, strict: true) — меньше мусора в параметрах и меньше падений в ваших обработчиках. Обзор «Structured Outputs» и строгих схем: <https://cookbook.openai.com/examples/structured_outputs_intro> .  ￼
 4. Жёсткое сопоставление call_id ↔ function_call_output + логирование каждого шага (какой тул, какие аргументы, сколько занял) — это устраняет типовые «рассинхроны» при нескольких вызовах. Формат сообщений показан в рецептах:
<https://cookbook.openai.com/examples/reasoning_function_calls> .  ￼

⸻

Микро-оптимизации
 • На длинных цепочках опирайтесь на previous_response_id, чтобы не пересобирать/не перетаскивать весь промпт и набор тулов каждый раз — контекст хранится на стороне API. Обзор в README (Responses как «первичный» путь) + рецепты с продолжением хода:
<https://github.com/openai/openai-python>
<https://cookbook.openai.com/examples/reasoning_function_calls> .  ￼
 • Если тулов много, фильтруйте доступный список под задачу (меньше «шума» при выборе и быстрее шаги). Практика отражена в рецептах по оркестрации: <https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼

⸻

Полезные примеры «как у них»
 • Chat Completions + function-calling (пошаговый паттерн, tool_calls, повторный запрос с ролью tool):
<https://cookbook.openai.com/examples/how_to_call_functions_with_chat_models> .  ￼
 • Responses API: многошаговый луп, function_call_output, смешивание хостовых и кастомных тулов, в т. ч. web-search:
<https://cookbook.openai.com/examples/responses_api/responses_api_tool_orchestration> .  ￼
 • Функциональные вызовы с продолжением через previous_response_id и явное сопоставление call_id:
<https://cookbook.openai.com/examples/reasoning_function_calls> .  ￼
 • Строгие схемы и «Structured Outputs» (как уменьшить ошибки форматирования):
<https://cookbook.openai.com/examples/structured_outputs_intro> .  ￼
 • README openai-python (структура SDK 2.x, акцент на Responses API):
<https://github.com/openai/openai-python> .  ￼

⸻

TL;DR чек-лист
 • Всегда реализуйте итеративный луп вокруг tool-calling.
 • Обрабатывайте несколько function_call за шаг; при необходимости включайте parallel_tool_calls.
 • Возвращайте выводы строго как function_call_output с тем же call_id.
 • Ставьте таймауты, ретраи, лимит шагов; логируйте каждый шаг.
 • Используйте строгие JSON-схемы и по возможности Structured Outputs.
 • В Responses полагайтесь на previous_response_id для устойчивых многошаговых ходов.
 • Комбинируйте хостовые тула (например, web-search) и свои; держите список тулов узким под задачу.
