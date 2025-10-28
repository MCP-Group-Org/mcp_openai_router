"""Вспомогательные функции для работы с OpenAI Responses API."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp_openai_router.services.openai_responses")

try:
    # Опциональная зависимость во время выполнения; в тестах фабрику можно замокать.
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


def create_openai_client() -> Any:
    """Создаёт клиент OpenAI, используя переменные окружения."""
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Install the 'openai' package.")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


class OpenAIClientAdapter:
    """Инкапсулирует фабрику клиента OpenAI и доступ к Responses API."""

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None) -> None:
        self._client_factory = client_factory or create_openai_client
        self._client: Optional[Any] = None
        self._create_fn: Optional[Callable[..., Any]] = None
        self._retrieve_fn: Optional[Callable[..., Any]] = None

    def ensure_ready(self) -> None:
        """Ленивая инициализация клиента и проверка доступности Responses API."""
        if self._client is not None and self._create_fn is not None:
            return

        client = self._client_factory()
        responses_api = getattr(client, "responses", None)
        if responses_api is None:
            raise RuntimeError("OpenAI client missing Responses API.")

        create_fn = getattr(responses_api, "create", None)
        if not callable(create_fn):
            raise RuntimeError("OpenAI client does not expose responses.create; update the SDK.")

        retrieve_candidate = getattr(responses_api, "retrieve", None)
        retrieve_fn = retrieve_candidate if callable(retrieve_candidate) else None

        self._client = client
        self._create_fn = create_fn
        self._retrieve_fn = retrieve_fn

    def create_response(self, payload: Dict[str, Any]) -> Any:
        """Создаёт ответ через Responses API."""
        self.ensure_ready()
        assert self._create_fn is not None
        return self._create_fn(**payload)

    def retrieve_response(self, response_id: str) -> Any:
        """Получает ответ по идентификатору, если доступен метод retrieve."""
        self.ensure_ready()
        if self._retrieve_fn is None:
            return None
        try:
            return self._retrieve_fn(response_id=response_id)
        except TypeError:
            return self._retrieve_fn(id=response_id)  # type: ignore[call-arg]

    def can_retrieve(self) -> bool:
        """Проверяет, доступен ли метод retrieve у Responses API."""
        self.ensure_ready()
        return self._retrieve_fn is not None


def maybe_model_dump(value: Any) -> Dict[str, Any]:
    """Возвращает словарь из Pydantic/датакласса/JSON-строки или пустой dict."""
    if value is None:
        return {}
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if hasattr(value, "dict") and callable(value.dict):
        return value.dict()  # type: ignore[return-value]
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)  # type: ignore[arg-type]
    except Exception:
        return {}


def convert_tool_call_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """Нормализует блок tool_call из ответа OpenAI."""
    arguments = block.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"raw": arguments}
    elif arguments is None:
        arguments = {}
    return {
        "id": block.get("call_id") or block.get("id") or block.get("tool_call_id"),
        "toolName": block.get("name") or block.get("tool_name"),
        "arguments": arguments,
    }


def normalise_responses_output(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Извлекает текстовые блоки и tool_calls из Responses API."""
    content_blocks: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []

    outputs = data.get("output") or data.get("outputs") or []
    for output in outputs:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON output entry: %s", output)
                continue
        if not isinstance(output, dict):
            continue

        output_type = output.get("type")
        if output_type == "message":
            for block in output.get("content", []):
                block_type = block.get("type")
                if block_type in {"output_text", "text", "input_text"}:
                    text = block.get("text") or block.get("value") or ""
                    if text:
                        content_blocks.append({"type": "text", "text": text})
                elif block_type in {"tool_call", "function_call"}:
                    tool_calls.append(convert_tool_call_block(block))
        elif output_type in {"tool_call", "function_call"}:
            tool_calls.append(convert_tool_call_block(output))
        elif output_type in {"output_text", "text"}:
            text = output.get("text") or ""
            if text:
                content_blocks.append({"type": "text", "text": text})

    usage = data.get("usage") or {}
    finish_reason = data.get("status") or data.get("finish_reason")
    metadata: Dict[str, Any] = {}
    if usage:
        metadata["usage"] = usage
    if finish_reason:
        metadata["finishReason"] = finish_reason
    if data.get("id"):
        metadata["responseId"] = data["id"]
    return content_blocks, tool_calls, metadata


def normalise_chat_completion(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Fallback для традиционного Chat Completions ответа."""
    content_blocks: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}

    choices = data.get("choices") or []
    if not choices:
        return content_blocks, tool_calls, metadata

    first = choices[0]
    message = first.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if text:
                    content_blocks.append({"type": "text", "text": text})
    elif isinstance(content, str) and content:
        content_blocks.append({"type": "text", "text": content})

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}
        tool_calls.append(
            {
                "id": call.get("id"),
                "toolName": function.get("name") or call.get("type"),
                "arguments": arguments or {},
            }
        )

    usage = data.get("usage") or {}
    finish_reason = first.get("finish_reason")
    if usage:
        metadata["usage"] = usage
    if finish_reason:
        metadata["finishReason"] = finish_reason
    if data.get("id"):
        metadata["responseId"] = data["id"]
    return content_blocks, tool_calls, metadata


class ChatArgError(ValueError):
    """Ошибка валидации аргументов chat-инструмента."""


def extract_chat_params(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Выделяет параметры chat-инструмента из аргументов."""
    model = arguments.get("model")
    messages = arguments.get("messages")
    if not isinstance(model, str):
        raise ChatArgError("Invalid params: 'model' must be a string")
    if not isinstance(messages, list):
        raise ChatArgError("Invalid params: 'messages' must be an array")

    return {
        "model": model,
        "messages": messages,
        "temperature": float(arguments.get("temperature", 0.7)),
        "top_p": arguments.get("top_p"),
        "max_tokens": arguments.get("max_tokens"),
        "metadata": arguments.get("metadata"),
        "parallel_tool_calls": arguments.get("parallelToolCalls"),
        "tools": arguments.get("tools"),
        "tool_choice": arguments.get("tool_choice") or arguments.get("toolChoice"),
    }


def normalize_input_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Подчищает входные сообщения перед отправкой в Responses API."""
    cleaned_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            raise ChatArgError("Invalid params: every message must be an object")
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str):
            raise ChatArgError("Invalid params: message role must be a string")
        if isinstance(content, list):
            cleaned = [item for item in content if isinstance(item, dict)]
            cleaned_messages.append({"role": role, "content": cleaned})
        else:
            cleaned_messages.append({"role": role, "content": content})
    return cleaned_messages


def build_request_payload(
    params: Dict[str, Any],
    input_messages: List[Dict[str, Any]],
    *,
    ensure_think_tool: bool,
) -> Dict[str, Any]:
    """Формирует полезную нагрузку для responses.create."""
    payload: Dict[str, Any] = {
        "model": params["model"],
        "input": input_messages,
        "temperature": params["temperature"],
    }
    tools_param = params.get("tools")
    tools: List[Dict[str, Any]] | None = None
    if isinstance(tools_param, list):
        tools = [item for item in tools_param if isinstance(item, dict)]
        if tools:
            payload["tools"] = tools
    if params.get("metadata"):
        payload["metadata"] = params["metadata"]
    if params.get("top_p") is not None:
        payload["top_p"] = float(params["top_p"])  # type: ignore[arg-type]
    if params.get("max_tokens") is not None:
        payload["max_output_tokens"] = int(params["max_tokens"])  # type: ignore[arg-type]
    if params.get("parallel_tool_calls") is not None:
        payload["parallel_tool_calls"] = bool(params["parallel_tool_calls"])  # type: ignore[arg-type]
    if params.get("tool_choice") is not None:
        payload["tool_choice"] = params["tool_choice"]

    if ensure_think_tool:
        think_entry = {
            "type": "function",
            "name": "think",
            "description": "Capture intermediate reasoning using the external think-tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "Thought text to be persisted by think-tool.",
                    },
                    "parent_trace_id": {
                        "type": "string",
                        "description": "Optional LangSmith trace identifier.",
                    },
                },
                "required": ["thought"],
                "additionalProperties": False,
            },
        }
        tools_list = payload.setdefault("tools", tools or [])
        already_present = any(
            isinstance(entry, dict)
            and (entry.get("function") or {}).get("name") == "think"
            for entry in tools_list
        )
        if not already_present:
            tools_list.append(think_entry)

    return payload


__all__ = [
    "ChatArgError",
    "build_request_payload",
    "convert_tool_call_block",
    "create_openai_client",
    "extract_chat_params",
    "maybe_model_dump",
    "normalise_chat_completion",
    "normalise_responses_output",
    "normalize_input_messages",
]
