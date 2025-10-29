"""Dataclasses для финальной сборки ответа chat-инструмента."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.tools.handlers import _tool_error, _tool_ok

from .think_processor import ThinkLogEntry

ToolResponse = Dict[str, Any]


@dataclass
class ProcessingResult:
    """Инкапсулирует результат обработки chat-запроса перед сериализацией."""

    content: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    think_logs: List[ThinkLogEntry] = field(default_factory=list)
    error_message: Optional[str] = None
    error_metadata: Optional[Dict[str, Any]] = None

    def is_error(self) -> bool:
        return self.error_message is not None

    def to_tool_response(self) -> ToolResponse:
        if self.is_error():
            result = _tool_error(self.error_message or "Processing failed", metadata=self.error_metadata)
            if self.think_logs:
                metadata = result.setdefault("metadata", {})
                metadata["thinkTool"] = [entry.to_dict() for entry in self.think_logs]
            return result

        result = _tool_ok(content=self.content, tool_calls=self.tool_calls, metadata=self.metadata)
        if self.think_logs:
            metadata = result.setdefault("metadata", {})
            metadata["thinkTool"] = [entry.to_dict() for entry in self.think_logs]
        return result


__all__ = ["ProcessingResult"]
