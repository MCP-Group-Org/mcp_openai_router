# app/main.py
"""Точка входа FastAPI, предоставляющая минимальный MCP-роутер к OpenAI.

Добавлено: поддержка hosted tools (напр., web_search) через OpenAI Responses API,
если в arguments переданы поля tools/tool_choice.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI

from .api import configure_routes, router as api_router
from .core.config import (
    ENABLE_LEGACY_METHODS,
    MAX_POLLS,
    POLL_DELAY,
    POLL_SEM,
    PROTOCOL_VERSION,
    REQUIRE_SESSION,
    SERVER_CAPABILITIES,
    SERVER_INFO,
    THINK_TOOL_CONFIG,
)
from .core.session import ACTIVE_SESSIONS
from .tools.handlers import _handle_echo, _handle_read_file, _handle_think, _tool_error, _tool_ok
from .tools.registry import ToolHandler, ToolResponse, ToolSchema, ToolSpec, TOOLS
from .services.openai_responses import (
    ChatArgError,
    build_request_payload,
    create_openai_client,
    extract_chat_params,
    maybe_model_dump,
    normalise_chat_completion,
    normalise_responses_output,
    normalize_input_messages,
)
from .think_client import ThinkToolConfig


logger = logging.getLogger("mcp_openai_router")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


# Тонкие обёртки сохранены ради тестов, которые monkeypatch по именам внутри app.main.
def _create_openai_client() -> Any:
    return create_openai_client()


def _maybe_model_dump(value: Any) -> Dict[str, Any]:
    return maybe_model_dump(value)

# =========================
# FastAPI app
# =========================
app = FastAPI(title="MCP - OpenAI Router", version="0.0.2")


# =========================
# MCP tool registry
# =========================


# === Chat tool handler ===
# Эта функция обрабатывает вызовы MCP-инструмента "chat". На вход получает
# словарь аргументов из JSON-RPC, валидирует его, формирует запрос к OpenAI
# Responses API (либо совместимый слой над ним), делает начальный запрос и при
# необходимости — дополнительные follow-up запросы (например, для think-инструмента),
# после чего нормализует ответ к универсальному формату ToolResponse.
# Идея: максимально изолировать контракт MCP (tools/call) от конкретики SDK OpenAI.
def _handle_chat(arguments: Dict[str, Any]) -> ToolResponse:
    # 1) Разбор аргументов chat-инструмента.
    # extract_chat_params выполняет строгую валидацию и нормализацию входа
    # (модель, сообщения, опции генерации и т.п.). Если формат неверный,
    # выбрасывается ChatArgError и мы возвращаем ToolResponse c ошибкой.
    try:
        params = extract_chat_params(arguments)
        input_messages = normalize_input_messages(params["messages"])  # type: ignore[arg-type]
    except ChatArgError as exc:
        return _tool_error(str(exc))

    # 2) Создание клиента OpenAI.
    # Обернули в тонкую функцию _create_openai_client ради тестов/monkeypatch.
    # Исключения транслируем в человекочитаемую ошибку инструмента.
    try:
        client = _create_openai_client()
    except RuntimeError as exc:
        return _tool_error(str(exc))

    # 3) Сборка полезной нагрузки для Responses API.
    # build_request_payload приводит параметры к нужной схеме SDK/HTTP, а также
    # при необходимости автоматически добавляет декларации инструментов (tools)
    # и конфигурацию think-инструмента, если это включено.
    request_payload = build_request_payload(params, input_messages, ensure_think_tool=THINK_TOOL_CONFIG.enabled)

    # 4) Дефенсивная проверка: у клиента должен быть раздел responses и метод create.
    # Мы не привязываемся жёстко к конкретному SDK: getattr позволяет безопасно
    # проверить наличие возможностей без падения при несовместимой версии.
    responses_api = getattr(client, "responses", None)
    if responses_api is None:
        return _tool_error("OpenAI client missing Responses API.")

    create_fn = getattr(responses_api, "create", None)
    if not callable(create_fn):
        return _tool_error("OpenAI client does not expose responses.create; update the SDK.")

    # Не все SDK поддерживают retrieve (получение статуса по id). Если нет —
    # мы сможем вернуть начальный ответ без активного опроса.
    retrieve_fn = getattr(responses_api, "retrieve", None)

    # 5) Инициализирующий запрос к Responses API.
    # Засекаем время ради логов и диагностик. Любая сетевая ошибка переводится
    # в ToolResponse c ошибкой — это позволит корректно отобразить её на стороне MCP-клиента.
    try:
        t0 = time.time()
        initial_response = create_fn(**request_payload)
        dt = (time.time() - t0) * 1000.0
        logger.info("responses.create ok in %.1f ms (model=%s, tools=%s)", dt, params["model"], bool(request_payload.get("tools")))
        response_data = _maybe_model_dump(initial_response)
    except Exception as exc:  # pragma: no cover - network failures
        logger.exception("OpenAI Responses API call failed on create")
        return _tool_error(f"OpenAI call failed: {exc}")

    # Настройки опроса статуса ответа (из конфигурации):
    #  - POLL_DELAY: пауза между попытками (сек.),
    #  - MAX_POLLS: ограничение числа попыток, чтобы не зависнуть в ожидании.
    poll_delay = POLL_DELAY
    max_polls = MAX_POLLS

    # 6) Вспомогательная функция опроса статуса ответа.
    # Используется, когда первоначальный статус — queued/in_progress, или когда
    # SDK вернул ответ без финального статуса. Реализуем аккуратный поллинг.
    def _poll_response(response_id: str, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not callable(retrieve_fn):
            return initial or {}
        # Ограничиваем параллельный поллинг через семафор POLL_SEM, чтобы не исчерпать
        # соединения/ресурсы клиента при массовых одновременных запросах.
        acquired = POLL_SEM.acquire(timeout=5.0)
        if not acquired:
            logger.warning("responses.retrieve semaphore timeout — skipping poll for %s", response_id)
            return initial or {}
        try:
            data = initial or {}
            status = data.get("status")
            # Основной цикл опроса: не более max_polls итераций. На каждом шаге пытаемся
            # получить свежие данные через responses.retrieve и проверяем статус.
            if status and status not in {"queued", "in_progress"}:
                return data
            polls = 0
            t_start = time.time()
            while polls < max_polls:
                t0 = time.time()
                try:
                    retrieved = retrieve_fn(response_id=response_id)
                except TypeError:
                    retrieved = retrieve_fn(id=response_id)  # type: ignore[call-arg]
                dt = (time.time() - t0) * 1000.0
                if not retrieved:
                    logger.info("responses.retrieve empty in %.1f ms (poll=%d)", dt, polls)
                    break
                data = _maybe_model_dump(retrieved)
                status = data.get("status")
                # Если пришёл терминальный статус (например, "completed" или "failed"),
                # возвращаем данные немедленно, фиксируя в логах общее время ожидания.
                if status and status not in {"queued", "in_progress"}:
                    total_ms = (time.time() - t_start) * 1000.0
                    logger.info("responses.retrieve terminal status=%s in %.1f ms after %d polls", status, total_ms, polls + 1)
                    return data
                polls += 1
                time.sleep(poll_delay)
            total_ms = (time.time() - t_start) * 1000.0
            logger.info("responses.retrieve hit poll limit after %d polls in %.1f ms (last status=%s)", polls, total_ms, status)
            return data
        finally:
            POLL_SEM.release()

    # 7) Унификация получения финального ответа.
    # Если статус уже финальный — просто возвращаем. Если статус не указан, но
    # есть retrieve, пытаемся дополучить данным по id. Это прячет разницу между
    # вариантами SDK и упрощает основную логику ниже.
    def _resolve_response(payload: Dict[str, Any]) -> Dict[str, Any]:
        status = payload.get("status")
        response_id = payload.get("id")
        if not response_id:
            return payload
        if status in {"queued", "in_progress"}:
            return _poll_response(response_id, payload)
        if status is None and callable(retrieve_fn):
            return _poll_response(response_id, payload)
        return payload

    # 8) Переменные для накопления результата и промежуточного состояния:
    #  - follow_up_data: текущий полный ответ от API,
    #  - final_meta: финальные метаданные (id ответа и т.п.),
    #  - think_logs: журнал выполнения think-инструмента,
    #  - final_content: собранный контент для пользователя,
    #  - remaining_tool_calls: вызовы инструментов, которые модель запросила, но их должен выполнить MCP-клиент.
    follow_up_data = _resolve_response(response_data)
    final_meta: Optional[Dict[str, Any]] = None
    think_logs: List[Dict[str, Any]] = []
    final_content: List[Dict[str, Any]] = []
    remaining_tool_calls: List[Dict[str, Any]] = []

    # Ограничение числа итераций (safety): если модель продолжает просить инструменты
    # бесконечно, мы не будем крутиться вечно. Это guardrail против зацикливания.
    max_turns = 15
    turn = 0

    # 9) Хелпер: приводим контент от think-инструмента к простому тексту.
    # Ответ think может быть массивом блоков; мы извлекаем текст и склеиваем
    # его, чтобы передать обратно в Responses API как function_call_output.
    def _convert_think_content(blocks: Optional[List[Dict[str, Any]]]) -> str:
        converted: List[str] = []
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                converted.append(text.strip())
        if not converted:
            return "ok"
        return "\n\n".join(converted)

    # 10) Основной цикл обработки ответа и инструментов.
    # На каждом шаге нормализуем ответ (responses/chat-completions),
    # проверяем, запросила ли модель инструменты, и либо завершаем,
    # либо исполняем think и отправляем follow-up.
    while turn < max_turns:
        turn += 1

        # Нормализация в единый формат. Разные режимы/модели возвращают отличающиеся
        # структуры; эти функции прячут различия и выдают:
        #  - content_blocks: массив блоков контента (text, etc),
        #  - tool_calls: список запросов к инструментам,
        #  - meta: полезные метаданные (responseId, статус и пр.).
        content_blocks, tool_calls, meta = normalise_responses_output(follow_up_data)
        if not content_blocks and not tool_calls:
            content_blocks, tool_calls, meta = normalise_chat_completion(follow_up_data)
        if not content_blocks and not tool_calls and follow_up_data:
            content_blocks = [{"type": "text", "text": json.dumps(follow_up_data)}]

        if meta:
            final_meta = meta

        if tool_calls:
            logger.info("Received tool calls: %s", tool_calls)

        # Если модель не запрашивает инструменты, считаем ответ финальным и выходим.
        if not tool_calls:
            final_content = content_blocks
            remaining_tool_calls = tool_calls
            break

        # Подготовка данных для возможного follow-up запроса: сюда соберём
        # результаты работы think-инструмента, чтобы отправить их обратно в модель.
        follow_up_inputs: List[Dict[str, Any]] = []
        remaining_tool_calls = []

        # Перебираем запрошенные инструментальные вызовы. Мы поддерживаем локально
        # только think: остальные оставляем "как есть", чтобы их обработал внешний MCP-клиент.
        for call in tool_calls:
            if call.get("toolName") != "think":
                remaining_tool_calls.append(call)
                continue

            logger.info("Processing think tool call: %s", call)
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"raw": arguments}

            # Вызываем think-инструмент локально. Он может вернуть content (логи/размышления)
            # или ошибку. Ведём журнал для диагностики и для включения в metadata ответа.
            think_result = _handle_think(arguments)
            think_logs.append(
                {
                    "callId": call.get("id"),
                    "status": "error" if think_result.get("isError") else "ok",
                    "result": think_result,
                }
            )

            # Если think завершился ошибкой, формируем человекочитаемое сообщение и
            # сразу возвращаем ToolResponse с ошибкой — дальнейшие follow-up не имеет смысла.
            if think_result.get("isError"):
                error_blocks = think_result.get("content") or [{"type": "text", "text": "think-tool returned error"}]
                error_texts = []
                for block in error_blocks:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        error_texts.append(block["text"])
                message = "\n".join(error_texts) or "think-tool returned error"
                return _tool_error(message, metadata=think_result.get("metadata"))

            tool_call_id = call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                return _tool_error("Invalid think-tool call identifier.")

            # Преобразуем результат think в формат function_call_output, который понимает
            # Responses API: связываем с исходным call_id и передаём текст в виде input_text.
            follow_up_inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": [
                        {
                            "type": "input_text",
                            "text": _convert_think_content(think_result.get("content")),
                        }
                    ],
                }
            )

        if follow_up_inputs:
            logger.info("Prepared function_call_output payloads: %s", follow_up_inputs)

        if not follow_up_inputs:
            final_content = content_blocks
            break

        # Чтобы сделать follow-up, нам нужен response_id предыдущего ответа. Берём его
        # из meta/финального meta/или из самого payload — в зависимости от того, что вернул SDK.
        response_id = (
            (meta or {}).get("responseId")
            or (final_meta or {}).get("responseId")
            or follow_up_data.get("id")
        )
        if not response_id:
            final_content = content_blocks
            break

        # 11) Формируем follow-up запрос: передаём модель, previous_response_id и input
        # (результаты think). По возможности передаём и metadata, чтобы сохранить связность.
        try:
            follow_up_payload: Dict[str, Any] = {
                "model": params["model"],
                "previous_response_id": response_id,
                "input": follow_up_inputs,
            }
            if params.get("metadata"):
                follow_up_payload["metadata"] = params["metadata"]

            logger.info("Sending OpenAI follow-up: %s", follow_up_payload)
            t1 = time.time()
            follow_up = create_fn(**follow_up_payload)
            dt1 = (time.time() - t1) * 1000.0
            logger.info("responses.create (follow-up) ok in %.1f ms", dt1)
            follow_up_data = _resolve_response(_maybe_model_dump(follow_up))
            if not follow_up_data.get("id"):
                follow_up_data["id"] = response_id
        except Exception as exc:  # pragma: no cover - network failures
            logger.exception("OpenAI follow-up call failed")
            return _tool_error(f"OpenAI follow-up call failed: {exc}")
    else:  # pragma: no cover - guardrail
        return _tool_error("Reached maximum tool iterations without completion.")

    # 12) Сборка итогового ToolResponse: контент + (неисполненные) tool_calls + метаданные.
    # Этот объект — унифицированный контракт для MCP-слоя над любым движком генерации.
    result = _tool_ok(content=final_content, tool_calls=remaining_tool_calls, metadata=final_meta or None)
    # Добавляем журнал выполнения think в metadata, чтобы клиент мог показать цепочку
    # вызовов/статусов пользователю или использовать в telemetry.
    if think_logs:
        metadata = result.setdefault("metadata", {})
        metadata["thinkTool"] = [
            {
                "callId": log.get("callId"),
                "status": log.get("status"),
                "content": log.get("result", {}).get("content"),
                "metadata": log.get("result", {}).get("metadata"),
            }
            for log in think_logs
        ]
    return result


TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "echo": _handle_echo,
    "read_file": _handle_read_file,
    "chat": _handle_chat,
}

if THINK_TOOL_CONFIG.enabled:
    TOOLS["think"] = ToolSpec(
        name="think",
        description="Если пользователь упоминает термин think, используй этот инструмент обязательно!",
        input_schema=ToolSchema(
            properties={
                "thought": {
                    "type": "string",
                    "description": "Thought text to be persisted by think-tool.",
                },
                "parent_trace_id": {
                    "type": "string",
                    "description": "Optional LangSmith trace identifier.",
                },
            },
            required=["thought"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array", "description": "Stream-friendly response blocks."},
                "metadata": {"type": "object", "description": "Additional context from think-tool."},
                "isError": {"type": "boolean"},
            },
        ),
    )
    TOOL_HANDLERS["think"] = _handle_think

configure_routes(tool_handlers=TOOL_HANDLERS, tools=TOOLS)
app.include_router(api_router)
