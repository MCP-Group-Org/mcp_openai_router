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


# ---- Chat tool helpers to reduce cognitive complexity ----
def _handle_chat(arguments: Dict[str, Any]) -> ToolResponse:
    try:
        params = extract_chat_params(arguments)
        input_messages = normalize_input_messages(params["messages"])  # type: ignore[arg-type]
    except ChatArgError as exc:
        return _tool_error(str(exc))

    try:
        client = _create_openai_client()
    except RuntimeError as exc:
        return _tool_error(str(exc))

    request_payload = build_request_payload(params, input_messages, ensure_think_tool=THINK_TOOL_CONFIG.enabled)

    responses_api = getattr(client, "responses", None)
    if responses_api is None:
        return _tool_error("OpenAI client missing Responses API.")

    create_fn = getattr(responses_api, "create", None)
    if not callable(create_fn):
        return _tool_error("OpenAI client does not expose responses.create; update the SDK.")

    retrieve_fn = getattr(responses_api, "retrieve", None)

    try:
        t0 = time.time()
        initial_response = create_fn(**request_payload)
        dt = (time.time() - t0) * 1000.0
        logger.info("responses.create ok in %.1f ms (model=%s, tools=%s)", dt, params["model"], bool(request_payload.get("tools")))
        response_data = _maybe_model_dump(initial_response)
    except Exception as exc:  # pragma: no cover - network failures
        logger.exception("OpenAI Responses API call failed on create")
        return _tool_error(f"OpenAI call failed: {exc}")

    poll_delay = 0.05
    max_polls = 20

    def _poll_response(response_id: str, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not callable(retrieve_fn):
            return initial or {}
        # Constrain concurrent polling to keep connection pool healthy
        acquired = POLL_SEM.acquire(timeout=5.0)
        if not acquired:
            logger.warning("responses.retrieve semaphore timeout — skipping poll for %s", response_id)
            return initial or {}
        try:
            data = initial or {}
            status = data.get("status")
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

    follow_up_data = _resolve_response(response_data)
    final_meta: Optional[Dict[str, Any]] = None
    think_logs: List[Dict[str, Any]] = []
    final_content: List[Dict[str, Any]] = []
    remaining_tool_calls: List[Dict[str, Any]] = []

    max_turns = 5
    turn = 0

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

    while turn < max_turns:
        turn += 1

        content_blocks, tool_calls, meta = normalise_responses_output(follow_up_data)
        if not content_blocks and not tool_calls:
            content_blocks, tool_calls, meta = normalise_chat_completion(follow_up_data)
        if not content_blocks and not tool_calls and follow_up_data:
            content_blocks = [{"type": "text", "text": json.dumps(follow_up_data)}]

        if meta:
            final_meta = meta

        if tool_calls:
            logger.info("Received tool calls: %s", tool_calls)

        if not tool_calls:
            final_content = content_blocks
            remaining_tool_calls = tool_calls
            break

        follow_up_inputs: List[Dict[str, Any]] = []
        remaining_tool_calls = []

        for call in tool_calls:
            if call.get("toolName") != "think":
                remaining_tool_calls.append(call)
                continue

            logger.info("Processing think tool call: %s", call)
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"raw": arguments}

            think_result = _handle_think(arguments)
            think_logs.append(
                {
                    "callId": call.get("id"),
                    "status": "error" if think_result.get("isError") else "ok",
                    "result": think_result,
                }
            )

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

        response_id = (
            (meta or {}).get("responseId")
            or (final_meta or {}).get("responseId")
            or follow_up_data.get("id")
        )
        if not response_id:
            final_content = content_blocks
            break

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

    result = _tool_ok(content=final_content, tool_calls=remaining_tool_calls, metadata=final_meta or None)
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
