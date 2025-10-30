"""Обработчики MCP-инструментов."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import BASE_DIR, THINK_TOOL_CLIENT, THINK_TOOL_CONFIG
from app.tools.registry import ToolResponse

logger = logging.getLogger("mcp_openai_router.tools.handlers")


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
    payload: ToolResponse = {
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
        logger.debug("think-tool disabled in configuration")
        return _tool_error("think-tool отключён в конфигурации.")
    if THINK_TOOL_CLIENT is None:
        logger.debug("think-tool client not initialised")
        return _tool_error("think-tool недоступен: клиент не инициализирован, проверьте логи.")

    logger.debug("_handle_think arguments: %s", arguments)
    thought = arguments.get("thought")
    if not isinstance(thought, str) or not thought.strip():
        logger.debug("Invalid think 'thought': %s", thought)
        return _tool_error("Invalid params: 'thought' must be a non-empty string")
    parent_trace = arguments.get("parent_trace_id")
    if parent_trace is not None and not isinstance(parent_trace, str):
        logger.debug("Invalid think parent_trace_id: %s", parent_trace)
        return _tool_error("Invalid params: 'parent_trace_id' must be a string")

    # Извлекаем metadata для передачи в think-tool (для LangSmith трассировки)
    langsmith_metadata = arguments.get("metadata")
    if langsmith_metadata is not None and not isinstance(langsmith_metadata, dict):
        langsmith_metadata = None

    try:
        call_result = THINK_TOOL_CLIENT.capture_thought(
            thought, parent_trace, langsmith_metadata=langsmith_metadata
        )
    except Exception as exc:  # pragma: no cover - сетевые ошибки фиксируются в логах
        logger.exception("think-tool call failed")
        return _tool_error(f"think-tool call failed: {exc}")

    logger.debug("think-tool call result: %s", call_result)
    if call_result.was_skipped:
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


__all__ = [
    "_handle_echo",
    "_handle_read_file",
    "_handle_think",
    "_safe_read_file",
    "_tool_error",
    "_tool_ok",
]
