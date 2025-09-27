"""Точка входа FastAPI, предоставляющая минимальный маршрутизатор Model Context
Protocol.

В модуле объединены:

* Небольшое приложение FastAPI, которое раскрывает эндпоинты здоровья,
  MCP-рукопожатия и JSON-RPC, совместимые с HTTP-транспортом MCP.
* Pydantic-модели, реализующие используемое маршрутизатором подмножество
  спецификации JSON-RPC 2.0.
* Облегчённый реестр определений инструментов вместе с обработчиками вызовов
  ``echo``, ``read_file`` и ``chat``.
* Вспомогательные утилиты для проверки входных данных, безопасного доступа к
  файловой системе и проксирования запросов чат-комплишнов в совместимый с
  OpenAI бэкенд.

Цель — показать, как сервер MCP может переводить вызовы инструментов в реальные
действия, оставаясь безопасным внутри контейнера (например, ограничивая доступ
к файлам каталогом ``/app``).
"""

# app/main.py
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, Optional, Dict, Literal, Callable, Awaitable
from pathlib import Path

try:
    # Optional import; the container may not have it until Dockerfile is updated
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE_PATH = ROOT_DIR / ".env"

# =========================
# Configuration
# =========================

class Settings(BaseSettings):
    """Конфигурация приложения через переменные окружения."""

    openai_api_key: str = Field(..., description="OpenAI API ключ")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", 
        description="Базовый URL OpenAI API"
    )
    default_model: str = Field(
        default="gpt-4.1-mini", 
        description="Модель по умолчанию"
    )

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

# Глобальный экземпляр настроек
settings = Settings()

# =========================
# FastAPI app
# =========================
app = FastAPI(title="MCP - OpenAI Router", version="0.1.0")

# -------- Health --------
@app.get("/health")
async def health():
    """Простая проверка живости для оркестраторов контейнеров."""
    return {"status": "ok"}


@app.get("/diagnostics")
async def diagnostics():
    """Небольшой отчёт о состоянии приложения без раскрытия секретов."""

    return {
        "status": "ok",
        "app": {
            "title": app.title,
            "version": app.version,
        },
        "openai": {
            "sdk_available": OpenAI is not None,
            "base_url": settings.openai_base_url,
            "default_model": settings.default_model,
            "api_key_set": bool(settings.openai_api_key),
        },
        "tools": {
            "count": len(TOOLS),
            "names": list(TOOLS.keys()),
        },
        "filesystem": {
            "base_dir": str(BASE_DIR),
            "base_dir_exists": BASE_DIR.exists(),
        },
    }

# -------- JSON-RPC 2.0 models --------
class JsonRpcRequest(BaseModel):
    """Входящий запрос в формате JSON-RPC 2.0."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Any] = None

class JsonRpcResponse(BaseModel):
    """Успешный ответ в формате JSON-RPC 2.0."""

    jsonrpc: Literal["2.0"] = "2.0"
    result: Any = None
    id: Optional[Any] = None

class JsonRpcErrorObj(BaseModel):
    """Объект ошибки JSON-RPC 2.0 с описанием сбоя."""

    code: int
    message: str
    data: Optional[Any] = None

class JsonRpcError(BaseModel):
    """Конверт JSON-RPC 2.0, оборачивающий объект ошибки."""

    jsonrpc: Literal["2.0"] = "2.0"
    error: JsonRpcErrorObj
    id: Optional[Any] = None

# -------- MCP handshake (GET) --------
@app.get("/mcp")
async def mcp_handshake():
    """Сообщает клиентам о поддержке MCP-транспорта во время рукопожатия."""
    return {"mcp": True, "transport": "http", "endpoint": "/mcp", "status": "ready"}

# =========================
# Minimal MCP tool registry
# =========================

class ToolSchema(BaseModel):
    """Фрагмент JSON Schema, описывающий вход MCP-инструмента."""

    type: Literal["object"] = "object"
    properties: Dict[str, Any]
    required: list[str] = Field(default_factory=list)
    additionalProperties: bool = False

class ToolSpec(BaseModel):
    """Представление определения MCP-инструмента в памяти."""

    name: str
    description: str
    input_schema: ToolSchema

# Определяем инструменты: echo, read_file, chat
TOOLS: Dict[str, ToolSpec] = {
    "echo": ToolSpec(
        name="echo",
        description="Echo text back.",
        input_schema=ToolSchema(
            properties={
                "text": {"type": "string", "description": "Text to echo"}
            },
            required=["text"],
            additionalProperties=False,
        ),
    ),
    "read_file": ToolSpec(
        name="read_file",
        description="Read a text file from the server's /app directory (relative path).",
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
            additionalProperties=False,
        ),
    ),
    "chat": ToolSpec(
        name="chat",
        description="Call an OpenAI-compatible router (Chat Completions).",
        input_schema=ToolSchema(
            properties={
                "model": {"type": "string", "description": "Model name, e.g. gpt-4.1-mini"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "system|user|assistant"},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                    "description": "Chat history in OpenAI format",
                },
                "temperature": {"type": "number", "description": "0-2", "default": 0.7},
                "max_tokens": {"type": "integer", "description": "Max tokens for the response"},
                "top_p": {"type": "number", "description": "Nucleus sampling"},
                "metadata": {"type": "object", "description": "Optional vendor-specific options"},
            },
            required=["model", "messages"],
            additionalProperties=False,
        ),
    ),
}

BASE_DIR = Path("/app").resolve()

def _get_model(requested_model: Optional[str]) -> str:
    """Возвращает имя модели чата из настроек или переданного параметра."""

    return requested_model or settings.default_model

def _safe_read_file(path: str, max_bytes: int = 200_000) -> Dict[str, Any]:
    """Безопасно читает текст из ``/app``, предотвращая выход из песочницы."""

    # запрет абсолютных путей и выхода за пределы /app
    p_raw = Path(path)
    if p_raw.is_absolute() or ".." in p_raw.parts:
        return {
            "path": str(p_raw),
            "size": 0,
            "text": "",
            "error": "Invalid path (absolute or traversal not allowed)",
        }
    target = (BASE_DIR / p_raw).resolve()
    if not str(target).startswith(str(BASE_DIR)):
        return {
            "path": str(p_raw),
            "size": 0,
            "text": "",
            "error": "Path escapes base directory",
        }
    try:
        data = target.read_bytes()[: max(1, int(max_bytes))]
        return {
            "path": str(p_raw),
            "size": len(data),
            "text": data.decode("utf-8", errors="replace"),
        }
    except FileNotFoundError:
        return {"path": str(p_raw), "size": 0, "text": "", "error": "File not found"}
    except Exception as e:
        return {
            "path": str(p_raw),
            "size": 0,
            "text": "",
            "error": f"{type(e).__name__}: {e}",
        }

# =========================
# JSON-RPC helpers
# =========================

def _invalid_params_error(message: str, req_id: Any) -> JsonRpcError:
    """Возвращает ошибку JSON-RPC об некорректных параметрах."""

    return JsonRpcError(
        error=JsonRpcErrorObj(code=-32602, message=message),
        id=req_id,
    )


def _tool_not_found_error(req_id: Any) -> JsonRpcError:
    """Возвращает ошибку JSON-RPC о неизвестном инструменте."""

    return JsonRpcError(
        error=JsonRpcErrorObj(
            code=-32601,
            message="Tool not found",
            data={"available": list(TOOLS.keys())},
        ),
        id=req_id,
    )


def _unimplemented_tool_error(req_id: Any) -> JsonRpcError:
    """Возвращает ошибку JSON-RPC для нереализованных инструментов."""

    return JsonRpcError(
        error=JsonRpcErrorObj(code=-32601, message="Tool handler not implemented"),
        id=req_id,
    )


ToolCallHandler = Callable[[Dict[str, Any], Any], JsonRpcResponse | JsonRpcError]
MethodHandler = Callable[[JsonRpcRequest], Awaitable[JsonRpcResponse | JsonRpcError]]


def _tool_echo(arguments: Dict[str, Any], req_id: Any) -> JsonRpcResponse:
    """Возвращает переданный текст без изменений."""

    text = arguments.get("text", "")
    return JsonRpcResponse(result={"text": str(text)}, id=req_id)


def _tool_read_file(arguments: Dict[str, Any], req_id: Any) -> JsonRpcResponse | JsonRpcError:
    """Читает текстовый файл относительно ``/app`` и возвращает содержимое."""

    path = arguments.get("path")
    max_bytes = int(arguments.get("max_bytes", 200_000))
    if not isinstance(path, str):
        return _invalid_params_error("Invalid params: 'path' must be string", req_id)
    rf = _safe_read_file(path, max_bytes=max_bytes)
    return JsonRpcResponse(result=rf, id=req_id)


def _tool_chat(arguments: Dict[str, Any], req_id: Any) -> JsonRpcResponse | JsonRpcError:
    """Проксирует запрос чат-комплишна в совместимый с OpenAI бэкенд."""

    if OpenAI is None:
        return JsonRpcError(
            error=JsonRpcErrorObj(
                code=-32603,
                message="OpenAI SDK not available. Install 'openai' package.",
            ),
            id=req_id,
        )

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    requested_model = arguments.get("model")
    messages = arguments.get("messages") or []
    temperature = arguments.get("temperature", 0.7)
    max_tokens = arguments.get("max_tokens")
    top_p = arguments.get("top_p")

    model = _get_model(requested_model)

    if not isinstance(messages, list):
        return _invalid_params_error("Invalid params: 'messages' must be array", req_id)

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            **({"max_tokens": int(max_tokens)} if max_tokens is not None else {}),
            **({"top_p": float(top_p)} if top_p is not None else {}),
        )
        choice = completion.choices[0]
        content = (choice.message.content if getattr(choice, "message", None) else None) or ""
        usage = getattr(completion, "usage", None)
        return JsonRpcResponse(
            result={
                "id": getattr(completion, "id", None),
                "model": getattr(completion, "model", model),
                "object": getattr(completion, "object", "chat.completion"),
                "created": getattr(completion, "created", None),
                "message": {"role": "assistant", "content": content},
                "finish_reason": getattr(choice, "finish_reason", None),
                "usage": usage.model_dump()
                if hasattr(usage, "model_dump") and callable(getattr(usage, "model_dump"))
                else (usage or None),
            },
            id=req_id,
        )
    except Exception as e:
        return JsonRpcError(
            error=JsonRpcErrorObj(
                code=-32000,
                message="Router call failed",
                data=str(e),
            ),
            id=req_id,
        )


TOOL_CALL_HANDLERS: Dict[str, ToolCallHandler] = {
    "echo": _tool_echo,
    "read_file": _tool_read_file,
    "chat": _tool_chat,
}


async def _handle_tools_list(req: JsonRpcRequest) -> JsonRpcResponse:
    """Возвращает список поддерживаемых MCP-инструментов и их схем."""

    result = {
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema.model_dump(),
            }
            for spec in TOOLS.values()
        ]
    }
    return JsonRpcResponse(result=result, id=req.id)


async def _handle_tools_call(req: JsonRpcRequest) -> JsonRpcResponse | JsonRpcError:
    """Маршрутизирует вызов JSON-RPC к соответствующему обработчику инструмента."""

    params = req.params or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    if not isinstance(name, str):
        return _tool_not_found_error(req.id)

    if not isinstance(arguments, dict):
        return _invalid_params_error("Invalid params: 'arguments' must be object", req.id)

    if name not in TOOLS:
        return _tool_not_found_error(req.id)

    handler = TOOL_CALL_HANDLERS.get(name)
    if handler is None:
        return _unimplemented_tool_error(req.id)

    return handler(arguments, req.id)


async def _handle_legacy_echo(req: JsonRpcRequest) -> JsonRpcResponse:
    """Поддерживает обратную совместимость с запросами ``tools.echo``."""

    params = req.params or {}
    text = params.get("text", "")
    return JsonRpcResponse(result={"echo": {"text": str(text)}, "method": req.method}, id=req.id)


async def _handle_legacy_read_file(req: JsonRpcRequest) -> JsonRpcResponse | JsonRpcError:
    """Поддерживает обратную совместимость с запросами ``tools.read_file``."""

    params = req.params or {}
    return _tool_read_file(params, req.id)


METHOD_HANDLERS: Dict[str, MethodHandler] = {
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


LEGACY_METHOD_HANDLERS: Dict[str, MethodHandler] = {
    "tools.echo": _handle_legacy_echo,
    "tools.read_file": _handle_legacy_read_file,
}

# =========================
# JSON-RPC dispatcher
# =========================

@app.post("/mcp")
async def mcp_rpc(req: JsonRpcRequest):
    """Основной диспетчер JSON-RPC для обнаружения и вызова инструментов MCP."""

    try:
        handler = METHOD_HANDLERS.get(req.method)
        if handler is not None:
            return await handler(req)

        legacy_handler = LEGACY_METHOD_HANDLERS.get(req.method)
        if legacy_handler is not None:
            return await legacy_handler(req)

        return JsonRpcError(
            error=JsonRpcErrorObj(
                code=-32601,
                message="Method not found",
                data={"method": req.method},
            ),
            id=req.id,
        )

    except Exception as e:
        return JsonRpcError(
            error=JsonRpcErrorObj(code=-32603, message="Internal error", data=str(e)),
            id=req.id,
        )
