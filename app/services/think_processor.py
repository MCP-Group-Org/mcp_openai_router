"""Вспомогательный обработчик для think-tool."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

ToolResponse = Dict[str, Any]

logger = logging.getLogger("mcp_openai_router.services.think_processor")


@dataclass
class ThinkLogEntry:
    """Лог одного вызова think-инструмента."""

    call_id: Optional[str]
    status: str
    result: ToolResponse

    def to_dict(self) -> Dict[str, Any]:
        return {
            "callId": self.call_id,
            "status": self.status,
            "content": self.result.get("content"),
            "metadata": self.result.get("metadata"),
        }


@dataclass
class ThinkProcessResult:
    """Результат обработки инструментальных вызовов за итерацию."""

    follow_up_inputs: List[Dict[str, Any]] = field(default_factory=list)
    remaining_calls: List[Dict[str, Any]] = field(default_factory=list)
    think_logs: List[ThinkLogEntry] = field(default_factory=list)
    error_message: Optional[str] = None
    error_metadata: Optional[Dict[str, Any]] = None

    def is_error(self) -> bool:
        return self.error_message is not None


class ThinkToolProcessor:
    """Минимальная обёртка вокруг `_handle_think` для текущего цикла обработки."""

    def __init__(self, think_handler: Callable[[Dict[str, Any]], ToolResponse]) -> None:
        self._think_handler = think_handler

    def process(self, tool_calls: List[Dict[str, Any]]) -> ThinkProcessResult:
        result = ThinkProcessResult()

        for call in tool_calls:
            tool_name = call.get("toolName")
            if tool_name != "think":
                result.remaining_calls.append(call)
                continue

            logger.info("Processing think tool call: %s", call)
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"raw": arguments}

            think_result = self._think_handler(arguments)
            log_entry = ThinkLogEntry(
                call_id=call.get("id"),
                status="error" if think_result.get("isError") else "ok",
                result=think_result,
            )
            result.think_logs.append(log_entry)

            if think_result.get("isError"):
                error_blocks = think_result.get("content") or [{"type": "text", "text": "think-tool returned error"}]
                error_texts = []
                for block in error_blocks:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            error_texts.append(text)
                message = "\n".join(error_texts) or "think-tool returned error"
                result.error_message = message
                result.error_metadata = think_result.get("metadata")
                return result

            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                result.error_message = "Invalid think-tool call identifier."
                return result

            result.follow_up_inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": [
                        {
                            "type": "input_text",
                            "text": self._convert_content(think_result.get("content")),
                        }
                    ],
                }
            )

        return result

    @staticmethod
    def _convert_content(blocks: Optional[List[Dict[str, Any]]]) -> str:
        converted: List[str] = []
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                converted.append(text.strip())
        if not converted:
            return "ok"
        return "\n\n".join(converted)


__all__ = [
    "ThinkToolProcessor",
    "ThinkLogEntry",
    "ThinkProcessResult",
]
