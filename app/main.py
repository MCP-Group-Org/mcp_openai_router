from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pathlib import Path

app = FastAPI(title="MCP Server")

@app.get("/health")
def health():
    return {"status": "ok"}

# ---- TOOLS (dev stubs) ----
class EchoIn(BaseModel):
    text: str
    upper: bool = False

class EchoOut(BaseModel):
    value: str
    length: int

@app.post("/tools/echo", response_model=EchoOut)
def tool_echo(body: EchoIn):
    val = body.text.upper() if body.upper else body.text
    return EchoOut(value=val, length=len(val))

@app.get("/tools/read_file")
def tool_read_file(
    path: str = Query(..., description="Относительный путь внутри /app, напр. README.md"),
    max_chars: int = Query(2000, ge=1, le=200000)
):
    base = Path("/app").resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="path escapes /app")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    text = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return {"path": str(target.relative_to(base)), "size": len(text), "content": text}
