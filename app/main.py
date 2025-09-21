from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Optional, Dict, Literal

# --- Инициализация приложения ---
app = FastAPI(title="MCP Server", version="0.1.0")

# --- Healthcheck ---
@app.get("/health")
async def health():
    return {"status": "ok"}

# --- JSON-RPC 2.0 модели (pydantic v2-совместимые) ---
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

# --- MCP endpoints ---
@app.get("/mcp")
async def mcp_handshake():
    """Простейший хэндшейк MCP."""
    return {"mcp": True, "transport": "http", "endpoint": "/mcp", "status": "ready"}

@app.post("/mcp")
async def mcp_rpc(req: JsonRpcRequest):
    """Эхо-заглушка JSON-RPC 2.0."""
    try:
        return JsonRpcResponse(result={"echo": req.params or {}, "method": req.method}, id=req.id)
    except Exception as e:
        return JsonRpcError(error=JsonRpcErrorObj(code=-32603, message="Internal error", data=str(e)), id=req.id)
