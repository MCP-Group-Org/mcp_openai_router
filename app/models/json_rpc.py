"""Pydantic-модели для JSON-RPC вызовов и состояния сессий MCP."""

from __future__ import annotations

from typing import Any, Dict, Optional, Literal

from pydantic import BaseModel, Field


class JsonRpcRequest(BaseModel):
    """Стандартный JSON-RPC 2.0 запрос."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Any] = None


class JsonRpcResponse(BaseModel):
    """Успешный JSON-RPC 2.0 ответ."""

    jsonrpc: Literal["2.0"] = "2.0"
    result: Any = None
    id: Optional[Any] = None


class JsonRpcErrorObj(BaseModel):
    """Структура ошибки JSON-RPC 2.0."""

    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 ответ с ошибкой."""

    jsonrpc: Literal["2.0"] = "2.0"
    error: JsonRpcErrorObj
    id: Optional[Any] = None


class InitializeParams(BaseModel):
    """Параметры метода `initialize` MCP."""

    protocolVersion: Optional[str] = None
    clientInfo: Dict[str, Any] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    """Минимальное состояние активной MCP-сессии."""

    id: str
    client_info: Dict[str, Any] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "InitializeParams",
    "JsonRpcError",
    "JsonRpcErrorObj",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "SessionState",
]
