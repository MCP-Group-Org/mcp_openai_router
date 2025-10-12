# app/main.py
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
app = FastAPI(title="MCP - OpenAI Router", version="0.2.0")


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
    "version": os.getenv("APP_VERSION", "0.2.0"),
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
        raise McpSessionError("Missing sessionId", code=-32602)
    session = ACTIVE_SESSIONS.get(session_id)
    if session is None:
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


def _handle_chat(arguments: Dict[str, Any]) -> ToolResponse:
    model = arguments.get("model")
    messages = arguments.get("messages")
    if not isinstance(model, str):
        return _tool_error("Invalid params: 'model' must be a string")
    if not isinstance(messages, list):
        return _tool_error("Invalid params: 'messages' must be an array")

    try:
        client = _create_openai_client()
    except RuntimeError as exc:
        return _tool_error(str(exc))

    temperature = arguments.get("temperature", 0.7)
    top_p = arguments.get("top_p")
    max_tokens = arguments.get("max_tokens")
    metadata = arguments.get("metadata")
    parallel_tool_calls = arguments.get("parallelToolCalls")

    input_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            return _tool_error("Invalid params: every message must be an object")
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str):
            return _tool_error("Invalid params: message role must be a string")
        if isinstance(content, list):
            cleaned = [item for item in content if isinstance(item, dict)]
            input_messages.append({"role": role, "content": cleaned})
        else:
            input_messages.append({"role": role, "content": content})

    request_payload: Dict[str, Any] = {
        "model": model,
        "input": input_messages,
        "temperature": float(temperature),
    }
    if metadata:
        request_payload["metadata"] = metadata
    if top_p is not None:
        request_payload["top_p"] = float(top_p)
    if max_tokens is not None:
        request_payload["max_output_tokens"] = int(max_tokens)
    if parallel_tool_calls is not None:
        request_payload["parallel_tool_calls"] = bool(parallel_tool_calls)

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
        if method == "initialize":
            try:
                parsed = InitializeParams.model_validate(params)
            except ValidationError as exc:
                return _json_rpc_error(-32602, "Invalid initialize params", data=exc.errors(), request_id=req.id)

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
            return JsonRpcResponse(result=result, id=req.id)

        if method == "ping":
            try:
                session = _require_session(params)
            except McpSessionError as exc:
                return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=req.id)
            return JsonRpcResponse(result={"sessionId": session.id}, id=req.id)

        if method == "shutdown":
            session_id = params.get("sessionId")
            if isinstance(session_id, str):
                ACTIVE_SESSIONS.pop(session_id, None)
            return JsonRpcResponse(result={}, id=req.id)

        if method == "tools/list":
            try:
                _require_session(params)
            except McpSessionError as exc:
                return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=req.id)
            result = {
                "tools": [spec.as_mcp_dict() for spec in TOOLS.values()],
                "nextCursor": None,
            }
            return JsonRpcResponse(result=result, id=req.id)

        if method == "tools/call":
            try:
                _require_session(params)
            except McpSessionError as exc:
                return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=req.id)

            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or name not in TOOL_HANDLERS:
                return _json_rpc_error(
                    -32601,
                    "Tool not found",
                    data={"available": list(TOOL_HANDLERS.keys())},
                    request_id=req.id,
                )
            if not isinstance(arguments, dict):
                return _json_rpc_error(
                    -32602,
                    "Invalid params: 'arguments' must be an object",
                    request_id=req.id,
                )
            handler = TOOL_HANDLERS[name]
            result = handler(arguments)
            return JsonRpcResponse(result=result, id=req.id)

        if ENABLE_LEGACY_METHODS and method in {"tools.echo", "tools.read_file"}:
            legacy_arguments = params if isinstance(params, dict) else {}
            if method == "tools.echo":
                return JsonRpcResponse(
                    result=_handle_echo(legacy_arguments),
                    id=req.id,
                )
            if method == "tools.read_file":
                return JsonRpcResponse(
                    result=_handle_read_file(legacy_arguments),
                    id=req.id,
                )

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
