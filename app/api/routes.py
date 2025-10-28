"""FastAPI-маршруты MCP API."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter
from pydantic import ValidationError

from app.core.config import (
    ENABLE_LEGACY_METHODS,
    PROTOCOL_VERSION,
    REQUIRE_SESSION,
    SERVER_CAPABILITIES,
    SERVER_INFO,
)
from app.core.session import ACTIVE_SESSIONS
from app.models.json_rpc import (
    InitializeParams,
    JsonRpcError,
    JsonRpcErrorObj,
    JsonRpcRequest,
    JsonRpcResponse,
    SessionState,
)
from app.tools.handlers import _handle_echo, _handle_read_file, _tool_error
from app.tools.registry import ToolHandler, ToolSpec

logger = logging.getLogger("mcp_openai_router.api.routes")

router = APIRouter()

_TOOL_HANDLERS: Dict[str, ToolHandler] = {}
_TOOLS: Dict[str, ToolSpec] = {}


def configure_routes(*, tool_handlers: Dict[str, ToolHandler], tools: Dict[str, ToolSpec]) -> None:
    """Инициализируем ссылки на реестр инструментов, чтобы избежать циклов импорта."""
    global _TOOL_HANDLERS, _TOOLS
    _TOOL_HANDLERS = tool_handlers
    _TOOLS = tools


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

# NOTE:
# All _handle_* functions below are intentionally synchronous (def, not async),
# because they perform only CPU-bound or simple in-memory operations and do not
# contain any 'await' expressions. This avoids unnecessary coroutine wrapping
# and satisfies SonarQube rule python:S7503 ("Use asynchronous features in this
# function or remove the async keyword").
# If future changes introduce actual async I/O, these can be safely converted
# back to 'async def'.

def _handle_initialize(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
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


def _handle_ping(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        session = _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)
    return JsonRpcResponse(result={"sessionId": session.id}, id=request_id)


def _handle_shutdown(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse:
    session_id = params.get("sessionId")
    if isinstance(session_id, str):
        ACTIVE_SESSIONS.pop(session_id, None)
    return JsonRpcResponse(result={}, id=request_id)


def _handle_tools_list(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)
    result = {
        "tools": [spec.as_mcp_dict() for spec in _TOOLS.values()],
        "nextCursor": None,
    }
    return JsonRpcResponse(result=result, id=request_id)


def _handle_tools_call(params: Dict[str, Any], request_id: Any) -> JsonRpcResponse | JsonRpcError:
    try:
        _require_session(params)
    except McpSessionError as exc:
        return _json_rpc_error(exc.code, str(exc), data=exc.data, request_id=request_id)

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or name not in _TOOL_HANDLERS:
        return _json_rpc_error(
            -32601,
            "Tool not found",
            data={"available": list(_TOOL_HANDLERS.keys())},
            request_id=request_id,
        )
    if not isinstance(arguments, dict):
        return _json_rpc_error(
            -32602,
            "Invalid params: 'arguments' must be an object",
            request_id=request_id,
        )
    handler = _TOOL_HANDLERS[name]
    result = handler(arguments)
    return JsonRpcResponse(result=result, id=request_id)


def _handle_legacy(params: Dict[str, Any], method: str, request_id: Any) -> JsonRpcResponse:
    legacy_arguments = params if isinstance(params, dict) else {}
    if method == "tools.echo":
        return JsonRpcResponse(result=_handle_echo(legacy_arguments), id=request_id)
    if method == "tools.read_file":
        return JsonRpcResponse(result=_handle_read_file(legacy_arguments), id=request_id)
    # Fallback should not occur due to caller checks; return method not found to be safe
    return JsonRpcResponse(result=_tool_error("Legacy method not supported"), id=request_id)


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.get("/mcp")
def mcp_info() -> Dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": SERVER_CAPABILITIES,
        "transport": {"type": "http", "endpoint": "/mcp"},
    }


@router.post("/mcp")
def mcp_rpc(req: JsonRpcRequest):
    method = req.method
    params = req.params or {}

    try:
        # Fast path dispatch table
        if method == "initialize":
            return _handle_initialize(params, req.id)
        if method == "ping":
            return _handle_ping(params, req.id)
        if method == "shutdown":
            return _handle_shutdown(params, req.id)
        if method == "tools/list":
            return _handle_tools_list(params, req.id)
        if method == "tools/call":
            return _handle_tools_call(params, req.id)

        # Optional legacy methods support
        if ENABLE_LEGACY_METHODS and method in {"tools.echo", "tools.read_file"}:
            return _handle_legacy(params, method, req.id)

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


__all__ = ["configure_routes", "router"]
