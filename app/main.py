# app/main.py
"""Точка входа FastAPI, предоставляющая минимальный MCP-роутер к OpenAI.

Добавлено: поддержка hosted tools (напр., web_search) через OpenAI Responses API,
если в arguments переданы поля tools/tool_choice.
"""
from __future__ import annotations

import copy
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
    OpenAIClientAdapter,
    ResponsePoller,
    build_request_payload,
    create_openai_client,
    extract_chat_params,
    maybe_model_dump,
    normalise_chat_completion,
    normalise_responses_output,
    normalize_input_messages,
)
from .utils.metadata import serialise_metadata_for_openai
from .services.langsmith_tracing import create_langsmith_tracer
from .services.chat_processing import ProcessingResult
from .services.think_processor import ThinkLogEntry, ThinkToolProcessor
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
    raw_metadata = arguments.get("metadata") if isinstance(arguments, dict) else None
    if isinstance(raw_metadata, dict):
        metadata_for_tracer: Optional[Dict[str, Any]] = raw_metadata
    else:
        metadata_for_tracer = None
    try:
        tracer_inputs = {"arguments": copy.deepcopy(arguments)}
    except Exception:
        tracer_inputs = {"arguments": arguments}
    tracer = create_langsmith_tracer(metadata_for_tracer)
    tracer.start(tracer_inputs)

    # 1) Разбор аргументов chat-инструмента.
    # extract_chat_params выполняет строгую валидацию и нормализацию входа
    # (модель, сообщения, опции генерации и т.п.). Если формат неверный,
    # выбрасывается ChatArgError и мы возвращаем ToolResponse c ошибкой.
    try:
        params = extract_chat_params(arguments)
        input_messages = normalize_input_messages(params["messages"])  # type: ignore[arg-type]
    except ChatArgError as exc:
        response = _tool_error(str(exc))
        return tracer.finalize_error(response, message=str(exc))

    # 2) Создание адаптера клиента OpenAI.
    # _create_openai_client оставлен для тестов/monkeypatch; адаптер добавляет
    # ленивую инициализацию и проверку Responses API.
    try:
        client_adapter = OpenAIClientAdapter(client_factory=_create_openai_client)
        client_adapter.ensure_ready()
    except RuntimeError as exc:
        message = str(exc)
        response = _tool_error(message)
        return tracer.finalize_error(response, message=message)

    # 3) Сборка полезной нагрузки для Responses API.
    # build_request_payload приводит параметры к нужной схеме SDK/HTTP, а также
    # при необходимости автоматически добавляет декларации инструментов (tools)
    # и конфигурацию think-инструмента, если это включено.
    request_payload = build_request_payload(params, input_messages, ensure_think_tool=THINK_TOOL_CONFIG.enabled)

    # 4) Проверяем доступность responses.retrieve — пригодится при поллинге.
    supports_retrieve = client_adapter.can_retrieve()

    # 5) Инициализирующий запрос к Responses API.
    # Засекаем время ради логов и диагностик. Любая сетевая ошибка переводится
    # в ToolResponse c ошибкой — это позволит корректно отобразить её на стороне MCP-клиента.
    try:
        t0 = time.time()
        initial_response = client_adapter.create_response(request_payload)
        dt = (time.time() - t0) * 1000.0
        logger.info("responses.create ok in %.1f ms (model=%s, tools=%s)", dt, params["model"], bool(request_payload.get("tools")))
        response_data = _maybe_model_dump(initial_response)
    except Exception as exc:  # pragma: no cover - network failures
        logger.exception("OpenAI Responses API call failed on create")
        message = f"OpenAI call failed: {exc}"
        response = _tool_error(message)
        return tracer.finalize_error(response, message=message)

    # Настройки опроса статуса ответа (из конфигурации):
    #  - POLL_DELAY: пауза между попытками (сек.),
    #  - MAX_POLLS: ограничение числа попыток, чтобы не зависнуть в ожидании.
    poll_delay = POLL_DELAY
    max_polls = MAX_POLLS
    poller = ResponsePoller(
        client_adapter,
        poll_delay=poll_delay,
        max_polls=max_polls,
        semaphore=POLL_SEM,
    )
    think_processor = ThinkToolProcessor(_handle_think)

    # 6) Вспомогательная функция опроса статуса ответа.
    # Используется, когда первоначальный статус — queued/in_progress, или когда
    # SDK вернул ответ без финального статуса. Реализуем аккуратный поллинг.
    def _poll_response(response_id: str, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return poller.poll(response_id, initial)

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
        if status is None and supports_retrieve:
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
    think_logs: List[ThinkLogEntry] = []
    final_content: List[Dict[str, Any]] = []
    remaining_tool_calls: List[Dict[str, Any]] = []

    # Ограничение числа итераций (safety): если модель продолжает просить инструменты
    # бесконечно, мы не будем крутиться вечно. Это guardrail против зацикливания.
    max_turns = 15
    turn = 0

    # 9) Основной цикл обработки ответа и инструментов.
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

        think_result = think_processor.process(tool_calls)
        think_logs.extend(think_result.think_logs)
        remaining_tool_calls = think_result.remaining_calls
        follow_up_inputs = think_result.follow_up_inputs

        if think_result.is_error():
            error_response = ProcessingResult(
                think_logs=think_logs,
                error_message=think_result.error_message,
                error_metadata=think_result.error_metadata,
            )
            error_payload = error_response.to_tool_response()
            error_text = think_result.error_message or "think-tool returned error"
            return tracer.finalize_error(error_payload, message=error_text)

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
                follow_up_payload["metadata"] = serialise_metadata_for_openai(params["metadata"])

            logger.info("Sending OpenAI follow-up: %s", follow_up_payload)
            t1 = time.time()
            follow_up = client_adapter.create_response(follow_up_payload)
            dt1 = (time.time() - t1) * 1000.0
            logger.info("responses.create (follow-up) ok in %.1f ms", dt1)
            follow_up_data = _resolve_response(_maybe_model_dump(follow_up))
            if not follow_up_data.get("id"):
                follow_up_data["id"] = response_id
        except Exception as exc:  # pragma: no cover - network failures
            logger.exception("OpenAI follow-up call failed")
            message = f"OpenAI follow-up call failed: {exc}"
            response = _tool_error(message)
            return tracer.finalize_error(response, message=message)
    else:  # pragma: no cover - guardrail
        message = "Reached maximum tool iterations without completion."
        response = _tool_error(message)
        return tracer.finalize_error(response, message=message)

    # 12) Сборка итогового ToolResponse: контент + (неисполненные) tool_calls + метаданные.
    processing_result = ProcessingResult(
        content=final_content,
        tool_calls=remaining_tool_calls,
        metadata=final_meta or None,
        think_logs=think_logs,
    )
    result_response = processing_result.to_tool_response()
    if processing_result.is_error():
        error_message = processing_result.error_message or "Processing failed"
        return tracer.finalize_error(result_response, message=error_message)
    return tracer.finalize_success(result_response)


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
