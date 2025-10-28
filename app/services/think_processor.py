"""Вспомогательный обработчик для think-tool."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

ToolResponse = Dict[str, Any]

logger = logging.getLogger("mcp_openai_router.services.think_processor")


class ThinkToolProcessor:
    """Минимальная обёртка вокруг `_handle_think` для текущего цикла обработки."""

    def __init__(self, think_handler: Callable[[Dict[str, Any]], ToolResponse]) -> None:
        self._think_handler = think_handler

    def process(
        self, tool_calls: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        follow_up_inputs: List[Dict[str, Any]] = []
        remaining_calls: List[Dict[str, Any]] = []
        think_logs: List[Dict[str, Any]] = []

        for call in tool_calls:
            tool_name = call.get("toolName")
            if tool_name != "think":
                remaining_calls.append(call)
                continue

            logger.info("Processing think tool call: %s", call)
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"raw": arguments}

            think_result = self._think_handler(arguments)
            think_logs.append(
                {
                    "callId": call.get("id"),
                    "status": "error" if think_result.get("isError") else "ok",
                    "result": think_result,
                }
            )

            if think_result.get("isError"):
                error_blocks = think_result.get("content") or [{"type": "text", "text": "think-tool returned error"}]
                error_texts = []
                for block in error_blocks:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            error_texts.append(text)
                message = "\n".join(error_texts) or "think-tool returned error"
                return follow_up_inputs, remaining_calls, think_logs, {
                    "message": message,
                    "metadata": think_result.get("metadata"),
                }

            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                return follow_up_inputs, remaining_calls, think_logs, {
                    "message": "Invalid think-tool call identifier.",
                    "metadata": None,
                }

            follow_up_inputs.append(
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

        return follow_up_inputs, remaining_calls, think_logs, None

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
