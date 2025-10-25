# app/main.py
"""Точка входа FastAPI, предоставляющая минимальный MCP-роутер к OpenAI.

Добавлено: поддержка hosted tools (напр., web_search) через OpenAI Responses API,
если в arguments переданы поля tools/tool_choice.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Literal
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field, ValidationError

from .think_client import ThinkToolClient, ThinkToolConfig, create_think_tool_client  # think-tool: конфиг+клиент

try:
    # Optional runtime dependency; in tests we patch the factory instead.
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


logger = logging.getLogger("mcp_openai_router")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# =========================
# FastAPI app
# =========================
app = FastAPI(title="MCP - OpenAI Router", version="0.0.2")


# -------- JSON-RPC 2.0 models --------
class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Any] = None


class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    result: Any = None
    id: Optional[Any] = None


class JsonRpcErrorObj(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    error: JsonRpcErrorObj
    id: Optional[Any] = None


# -------- MCP models --------
class InitializeParams(BaseModel):
    protocolVersion: Optional[str] = None
    clientInfo: Dict[str, Any] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    id: str
    client_info: Dict[str, Any] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


# =========================
# Constants and feature flags
# =========================
PROTOCOL_VERSION = "1.0"
SERVER_INFO = {
    "name": "mcp-openai-router",
    "version": os.getenv("APP_VERSION", "0.0.2"),
}
SERVER_CAPABILITIES = {
    "tools": {
        "listChangedNotification": False,
        "parallelCalls": True,
    },
    "sampling": {
        "supportsHostedTools": True,
    },
}
ENABLE_LEGACY_METHODS = (
    "--legacy" in sys.argv
    or os.getenv("MCP_ENABLE_LEGACY", "").lower() in {"1", "true", "yes"}
)
BASE_DIR = Path("/app").resolve()
ACTIVE_SESSIONS: Dict[str, SessionState] = {}
REQUIRE_SESSION = os.getenv("MCP_REQUIRE_SESSION", "1").strip().lower() in {"1", "true", "yes", "on"}

# think-tool: фиксируем настройки/клиент на уровне модуля, чтобы переиспользовать внутри обработчиков
THINK_TOOL_CONFIG = ThinkToolConfig.from_env()
THINK_TOOL_CLIENT: Optional[ThinkToolClient] = create_think_tool_client(THINK_TOOL_CONFIG)


# =========================
# MCP tool registry
# =========================
class ToolSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)
    additionalProperties: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: ToolSchema
    output_schema: Optional[ToolSchema] = None

    def as_mcp_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema.as_dict(),
        }
        if self.output_schema is not None:
            payload["outputSchema"] = self.output_schema.as_dict()
        return payload


ToolResponse = Dict[str, Any]
ToolHandler = Callable[[Dict[str, Any]], ToolResponse]


TOOLS: Dict[str, ToolSpec] = {
    "echo": ToolSpec(
        name="echo",
        description="Echo text back.",
        input_schema=ToolSchema(
            properties={
                "text": {"type": "string", "description": "Text to echo"},
            },
            required=["text"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array", "description": "Single text block"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
    "read_file": ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file from the server's /app directory (relative path).",
        input_schema=ToolSchema(
            properties={
                "path": {"type": "string", "description": "Relative path under /app"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Max bytes to read",
                    "minimum": 1,
                    "default": 200_000,
                },
            },
            required=["path"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
    "chat": ToolSpec(
        name="chat",
        description="Call an OpenAI Responses API compatible endpoint.",
        input_schema=ToolSchema(
            properties={
                "model": {"type": "string", "description": "Model name, e.g. gpt-4.1-mini"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "system|user|assistant|tool"},
                            "content": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "object"}},
                                ]
                            },
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                    "description": "Conversation history in OpenAI chat format.",
                },
                "temperature": {"type": "number", "description": "0-2 range", "default": 0.7},
                "max_tokens": {"type": "integer", "description": "Max output tokens for the response"},
                "top_p": {"type": "number", "description": "Nucleus sampling"},
                # ❗ Новое: для hosted tools (например, web_search в Responses API)
                "tools": {
                    "type": "array",
                    "description": "Hosted tools for Responses API (e.g., [{'type':'web_search'}]).",
                    "items": {"type": "object"},
                },
                "tool_choice": {
                    "type": "string",
                    "description": "Tool choice mode for Responses API (e.g., 'auto').",
                },
                "metadata": {"type": "object", "description": "Optional vendor-specific options"},
                "parallelToolCalls": {
                    "type": "boolean",
                    "description": "Allow hosted tools to run in parallel",
                },
            },
            required=["model", "messages"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array"},
                "toolCalls": {"type": "array"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
}


# =========================
# Helper utilities
# =========================
def _json_rpc_error(code: int, message: str, *, data: Any = None, request_id: Any = None) -> JsonRpcError:
    return JsonRpcError(
        error=JsonRpcErrorObj(code=code, message=message, data=data),
        id=request_id,
    )


class McpSessionError(Exception):
    def __init__(self, message: str, *, code: int = -32002, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def _require_session(params: Dict[str, Any]) -> SessionState:
    session_id = params.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        if not REQUIRE_SESSION:
            session_id = "_auto"
            session = ACTIVE_SESSIONS.get(session_id)
            if session is None:
                session = SessionState(id=session_id)
                ACTIVE_SESSIONS[session_id] = session
            params["sessionId"] = session_id
            return session
        raise McpSessionError("Missing sessionId", code=-32602)
    session = ACTIVE_SESSIONS.get(session_id)
    if session is None:
        if not REQUIRE_SESSION:
            session = SessionState(id=session_id)
            ACTIVE_SESSIONS[session_id] = session
        else:
            raise McpSessionError(f"Unknown sessionId '{session_id}'", code=-32003)
    return session


def _tool_ok(
    *,
    content: Optional[List[Dict[str, Any]]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ToolResponse:
    payload: ToolResponse = {
        "content": content or [],
        "toolCalls": tool_calls or [],
        "isError": False,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _tool_error(message: str, *, metadata: Optional[Dict[str, Any]] = None) -> ToolResponse:
    payload = {
        "content": [{"type": "text", "text": message}],
        "toolCalls": [],
        "isError": True,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _safe_read_file(path: str, *, max_bytes: int = 200_000) -> Dict[str, Any]:
    raw = Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        return {
            "error": "Invalid path (absolute paths and traversal are not allowed)",
            "path": str(raw),
            "text": "",
            "size": 0,
        }
    target = (BASE_DIR / raw).resolve()
    if not str(target).startswith(str(BASE_DIR)):
        return {
            "error": "Path escapes base directory",
            "path": str(raw),
            "text": "",
            "size": 0,
        }
    try:
        data = target.read_bytes()[: max(1, int(max_bytes))]
    except FileNotFoundError:
        return {"error": "File not found", "path": str(raw), "text": "", "size": 0}
    except Exception as exc:  # pragma: no cover - guardrail
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "path": str(raw),
            "text": "",
            "size": 0,
        }
    return {
        "path": str(raw),
        "size": len(data),
        "text": data.decode("utf-8", errors="replace"),
    }


def _create_openai_client() -> Any:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Install the 'openai' package.")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def _maybe_model_dump(value: Any) -> Dict[str, Any]:
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


def _convert_tool_call_block(block: Dict[str, Any]) -> Dict[str, Any]:
    arguments = block.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"raw": arguments}
    elif arguments is None:
        arguments = {}
    return {
        "id": block.get("id") or block.get("tool_call_id"),
        "toolName": block.get("name") or block.get("tool_name"),
        "arguments": arguments,
    }


def _normalise_responses_output(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    content_blocks: List[Dict[str, Any]] = []
    tool_calls: List[Dict[str, Any]] = []

    outputs = data.get("output") or data.get("outputs") or []
    for output in outputs:
        output_type = output.get("type")
        if output_type == "message":
            for block in output.get("content", []):
                block_type = block.get("type")
                if block_type in {"output_text", "text", "input_text"}:
                    text = block.get("text") or block.get("value") or ""
                    if text:
                        content_blocks.append({"type": "text", "text": text})
                elif block_type == "tool_call":
                    tool_calls.append(_convert_tool_call_block(block))
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


def _normalise_chat_completion(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
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


def _handle_echo(arguments: Dict[str, Any]) -> ToolResponse:
    text = arguments.get("text")
    if not isinstance(text, str):
        return _tool_error("Invalid params: 'text' must be a string")
    return _tool_ok(content=[{"type": "text", "text": text}])


def _handle_read_file(arguments: Dict[str, Any]) -> ToolResponse:
    path = arguments.get("path")
    if not isinstance(path, str):
        return _tool_error("Invalid params: 'path' must be a string")
    max_bytes_raw = arguments.get("max_bytes", 200_000)
    try:
        max_bytes = int(max_bytes_raw)
    except Exception:
        return _tool_error("Invalid params: 'max_bytes' must be an integer")
    result = _safe_read_file(path, max_bytes=max_bytes)
    if result.get("error"):
        return _tool_error(result["error"], metadata={"path": result.get("path")})
    metadata = {"path": result["path"], "size": result["size"]}
    return _tool_ok(content=[{"type": "text", "text": result["text"]}], metadata=metadata)

def _handle_think(arguments: Dict[str, Any]) -> ToolResponse:
    if not THINK_TOOL_CONFIG.enabled:
        return _tool_error("think-tool отключён в конфигурации.")
    if THINK_TOOL_CLIENT is None:
        return _tool_error("think-tool недоступен: клиент не инициализирован, проверьте логи.")

    thought = arguments.get("thought")
    if not isinstance(thought, str) or not thought.strip():
        return _tool_error("Invalid params: 'thought' must be a non-empty string")
    parent_trace = arguments.get("parent_trace_id")
    if parent_trace is not None and not isinstance(parent_trace, str):
        return _tool_error("Invalid params: 'parent_trace_id' must be a string")

    try:
        call_result = THINK_TOOL_CLIENT.capture_thought(thought, parent_trace)
    except Exception as exc:  # pragma: no cover - сетевые ошибки фиксируются в логах
        logger.exception("think-tool call failed")
        return _tool_error(f"think-tool call failed: {exc}")

    if call_result.skipped:
        return _tool_error(call_result.error or "think-tool request skipped by client")
    if not call_result.ok:
        metadata = {"status_code": call_result.status_code} if call_result.status_code else None
        return _tool_error(call_result.error or "think-tool returned error", metadata=metadata)

    remote_result = call_result.result or {}
    content: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = {"via": "think-tool"}

    if isinstance(remote_result, dict):
        remote_content = remote_result.get("content")
        if isinstance(remote_content, list):
            content = [item for item in remote_content if isinstance(item, dict)]
        if remote_result:
            metadata["remoteResult"] = remote_result

    if content is None:
        serialized = json.dumps(remote_result, ensure_ascii=False) if remote_result else "ok"
        content = [{"type": "text", "text": serialized}]

    return _tool_ok(content=content, metadata=metadata)


# ---- Chat tool helpers to reduce cognitive complexity ----
class _ChatArgError(ValueError):
    pass


def _extract_chat_params(arguments: Dict[str, Any]) -> Dict[str, Any]:
    model = arguments.get("model")
    messages = arguments.get("messages")
    if not isinstance(model, str):
        raise _ChatArgError("Invalid params: 'model' must be a string")
    if not isinstance(messages, list):
        raise _ChatArgError("Invalid params: 'messages' must be an array")

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


def _normalize_input_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            raise _ChatArgError("Invalid params: every message must be an object")
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str):
            raise _ChatArgError("Invalid params: message role must be a string")
        if isinstance(content, list):
            cleaned = [item for item in content if isinstance(item, dict)]
            cleaned_messages.append({"role": role, "content": cleaned})
        else:
            cleaned_messages.append({"role": role, "content": content})
    return cleaned_messages


def _build_request_payload(params: Dict[str, Any], input_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    if THINK_TOOL_CONFIG.enabled:
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
        # Avoid дублирования, если клиент уже передал think.
        already_present = any(
            isinstance(entry, dict)
            and (entry.get("function") or {}).get("name") == "think"
            for entry in tools_list
        )
        if not already_present:
            tools_list.append(think_entry)

    return payload


def _handle_chat(arguments: Dict[str, Any]) -> ToolResponse:
    try:
        params = _extract_chat_params(arguments)
        input_messages = _normalize_input_messages(params["messages"])  # type: ignore[arg-type]
    except _ChatArgError as exc:
        return _tool_error(str(exc))

    try:
        client = _create_openai_client()
    except RuntimeError as exc:
        return _tool_error(str(exc))

    request_payload = _build_request_payload(params, input_messages)

    try:
        responses_api = getattr(client, "responses")
        if hasattr(responses_api, "create_and_poll"):
            response = responses_api.create_and_poll(**request_payload)
        else:
            response = responses_api.create(**request_payload)
        response_data = _maybe_model_dump(response)
    except Exception as exc:  # pragma: no cover - network failures
        logger.exception("OpenAI Responses API call failed")
        return _tool_error(f"OpenAI call failed: {exc}")

    content_blocks, tool_calls, meta = _normalise_responses_output(response_data)
    if not content_blocks and not tool_calls:
        # Fallback to chat completions style payloads (legacy routers).
        content_blocks, tool_calls, meta = _normalise_chat_completion(response_data)
    if not content_blocks and not tool_calls and response_data:
        content_blocks = [{"type": "text", "text": json.dumps(response_data)}]
    return _tool_ok(content=content_blocks, tool_calls=tool_calls, metadata=meta or None)


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


# =========================
# RPC method handlers (extracted to reduce cognitive complexity in mcp_rpc)
# =========================
async def _handle_initialize(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        parsed = InitializeParams.model_validate(params)
    except ValidationError as exc:
        return _json_rpc_error(-32602, "Invalid initialize params", data=exc.errors(), request_id=request_id)

    session_id = str(uuid4())
    ACTIVE_SESSIONS[session_id] = SessionState(
        id=session_id,
        client_info=parsed.clientInfo,
        capabilities=parsed.capabilities,
    )
    result = {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "capabilities": SERVER_CAPABILITIES,
        "sessionId": session_id,
    }
    return JsonRpcResponse(result=result, id=request_id)

async def _handle_ping(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        session = _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)
    return JsonRpcResponse(result={"sessionId": session.id}, id=request_id)

async def _handle_shutdown(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse:
    session_id = params.get("sessionId")
    if isinstance(session_id, str):
        ACTIVE_SESSIONS.pop(session_id, None)
    return JsonRpcResponse(result={}, id=request_id)

async def _handle_tools_list(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)
    result = {
        "tools": [spec.as_mcp_dict() for spec in TOOLS.values()],
        "nextCursor": None,
    }
    return JsonRpcResponse(result=result, id=request_id)

async def _handle_tools_call(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or name not in TOOL_HANDLERS:
        return _json_rpc_error(
            -32601,
            "Tool not found",
            data={"available": list(TOOL_HANDLERS.keys())},
            request_id=request_id,
        )
    if not isinstance(arguments, dict):
        return _json_rpc_error(
            -32602,
            "Invalid params: 'arguments' must be an object",
            request_id=request_id,
        )
    handler = TOOL_HANDLERS[name]
    result = handler(arguments)
    return JsonRpcResponse(result=result, id=request_id)

async def _handle_legacy(params: Dict[str, Any], method: str, request_id: Any) -> JsonRpcResponse:
    legacy_arguments = params if isinstance(params, dict) else {}
    if method == "tools.echo":
        return JsonRpcResponse(result=_handle_echo(legacy_arguments), id=request_id)
    if method == "tools.read_file":
        return JsonRpcResponse(result=_handle_read_file(legacy_arguments), id=request_id)
    # Fallback should not occur due to caller checks; return method not found to be safe
    return JsonRpcResponse(result=_tool_error("Legacy method not supported"), id=request_id)

# =========================
# FastAPI routes
# =========================
@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/mcp")
async def mcp_info() -> Dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": SERVER_CAPABILITIES,
        "transport": {"type": "http", "endpoint": "/mcp"},
    }


@app.post("/mcp")
async def mcp_rpc(req: JsonRpcRequest):
    method = req.method
    params = req.params or {}

    try:
        # Fast path dispatch table
        if method == "initialize":
            return await _handle_initialize(params, req.id)
        if method == "ping":
            return await _handle_ping(params, req.id)
        if method == "shutdown":
            return await _handle_shutdown(params, req.id)
        if method == "tools/list":
            return await _handle_tools_list(params, req.id)
        if method == "tools/call":
            return await _handle_tools_call(params, req.id)

        # Optional legacy methods support
        if ENABLE_LEGACY_METHODS and method in {"tools.echo", "tools.read_file"}:
            return await _handle_legacy(params, method, req.id)

        return _json_rpc_error(
            -32601,
            "Method not found",
            data={"method": method},
            request_id=req.id,
        )

    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=req.id)
    except Exception as exc:  # pragma: no cover
        logger.exception("Unhandled MCP error")
        return _json_rpc_error(-32603, "Internal error", data=str(exc), request_id=req.id)
