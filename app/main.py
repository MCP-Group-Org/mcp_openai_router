# app/main.py
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any, Optional, Dict, Literal
from pathlib import Path
import os

try:
    # Optional import; the container may not have it until Dockerfile is updated
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

# =========================
# FastAPI app
# =========================
app = FastAPI(title="MCP Server", version="0.1.0")

# -------- Health --------
@app.get("/health")
async def health():
    return {"status": "ok"}

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

# -------- MCP handshake (GET) --------
@app.get("/mcp")
async def mcp_handshake():
    return {"mcp": True, "transport": "http", "endpoint": "/mcp", "status": "ready"}

# =========================
# Minimal MCP tool registry
# =========================

class ToolSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: Dict[str, Any]
    required: list[str] = Field(default_factory=list)
    additionalProperties: bool = False

class ToolSpec(BaseModel):
    name: str
    description: str
    schema: ToolSchema

# Определяем инструменты: echo, read_file, chat
TOOLS: Dict[str, ToolSpec] = {
    "echo": ToolSpec(
        name="echo",
        description="Echo text back.",
        schema=ToolSchema(
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
        schema=ToolSchema(
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
        schema=ToolSchema(
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

def _safe_read_file(path: str, max_bytes: int = 200_000) -> Dict[str, Any]:
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
# JSON-RPC dispatcher
# =========================

@app.post("/mcp")
async def mcp_rpc(req: JsonRpcRequest):
    try:
        method = req.method
        params = req.params or {}

        # --- MCP core: tools/list ---
        if method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.schema.model_dump(),
                    }
                    for spec in TOOLS.values()
                ]
            }
            return JsonRpcResponse(result=result, id=req.id)

        # --- MCP core: tools/call ---
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or name not in TOOLS:
                return JsonRpcError(
                    error=JsonRpcErrorObj(
                        code=-32601,
                        message="Tool not found",
                        data={"available": list(TOOLS.keys())},
                    ),
                    id=req.id,
                )
            # маршрутизация инструментов
            if name == "echo":
                text = arguments.get("text", "")
                return JsonRpcResponse(result={"text": str(text)}, id=req.id)

            if name == "read_file":
                path = arguments.get("path")
                max_bytes = int(arguments.get("max_bytes", 200_000))
                if not isinstance(path, str):
                    return JsonRpcError(
                        error=JsonRpcErrorObj(
                            code=-32602,
                            message="Invalid params: 'path' must be string",
                        ),
                        id=req.id,
                    )
                rf = _safe_read_file(path, max_bytes=max_bytes)
                return JsonRpcResponse(result=rf, id=req.id)

            if name == "chat":
                if OpenAI is None:
                    return JsonRpcError(
                        error=JsonRpcErrorObj(
                            code=-32603,
                            message="OpenAI SDK not available. Install 'openai' package.",
                        ),
                        id=req.id,
                    )
                api_key = os.getenv("OPENAI_API_KEY")
                base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
                if not api_key:
                    return JsonRpcError(
                        error=JsonRpcErrorObj(
                            code=-32602,
                            message="Missing OPENAI_API_KEY env var",
                        ),
                        id=req.id,
                    )
                client = OpenAI(api_key=api_key, base_url=base_url)

                model = arguments.get("model")
                messages = arguments.get("messages") or []
                temperature = arguments.get("temperature", 0.7)
                max_tokens = arguments.get("max_tokens", None)
                top_p = arguments.get("top_p", None)

                if not isinstance(model, str) or not isinstance(messages, list):
                    return JsonRpcError(
                        error=JsonRpcErrorObj(
                            code=-32602,
                            message="Invalid params: 'model' must be string and 'messages' must be array",
                        ),
                        id=req.id,
                    )

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
                            "usage": usage.model_dump() if hasattr(usage, "model_dump") and callable(getattr(usage, "model_dump")) else (usage or None),
                        },
                        id=req.id,
                    )
                except Exception as e:
                    return JsonRpcError(
                        error=JsonRpcErrorObj(
                            code=-32000,
                            message="Router call failed",
                            data=str(e),
                        ),
                        id=req.id,
                    )

            # защита от неучтённых
            return JsonRpcError(
                error=JsonRpcErrorObj(
                    code=-32601, message="Tool handler not implemented"
                ),
                id=req.id,
            )

        # --- Backward-compat aliases (не обязателен, но удобно):
        # tools.echo → echo, tools.read_file → read_file
        if method == "tools.echo":
            text = (params or {}).get("text", "")
            return JsonRpcResponse(result={"echo": {"text": str(text)}, "method": method}, id=req.id)

        if method == "tools.read_file":
            path = (params or {}).get("path")
            max_bytes = int((params or {}).get("max_bytes", 200_000))
            if not isinstance(path, str):
                return JsonRpcError(
                    error=JsonRpcErrorObj(code=-32602, message="Invalid params: 'path' must be string"),
                    id=req.id,
                )
            rf = _safe_read_file(path, max_bytes=max_bytes)
            return JsonRpcResponse(result=rf, id=req.id)

        # --- Unknown method ---
        return JsonRpcError(
            error=JsonRpcErrorObj(code=-32601, message="Method not found", data={"method": method}),
            id=req.id,
        )

    except Exception as e:
        return JsonRpcError(
            error=JsonRpcErrorObj(code=-32603, message="Internal error", data=str(e)),
            id=req.id,
        )
