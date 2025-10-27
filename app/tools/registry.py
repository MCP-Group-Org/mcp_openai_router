"""Описание схем и реестра MCP-инструментов приложения."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

ToolResponse = Dict[str, Any]
ToolHandler = Callable[[Dict[str, Any]], ToolResponse]


class ToolSchema(BaseModel):
    """JSON-схема аргументов/результатов инструмента MCP."""

    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)
    additionalProperties: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ToolSpec(BaseModel):
    """Спецификация инструмента MCP, публикуемая в `tools/list`."""

    name: str
    description: str
    input_schema: ToolSchema
    output_schema: Optional[ToolSchema] = None

    def as_mcp_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema.as_dict(),
        }
        if self.output_schema is not None:
            payload["outputSchema"] = self.output_schema.as_dict()
        return payload


TOOLS: Dict[str, ToolSpec] = {
    "echo": ToolSpec(
        name="echo",
        description="Echo text back.",
        input_schema=ToolSchema(
            properties={
                "text": {"type": "string", "description": "Text to echo"},
            },
            required=["text"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array", "description": "Single text block"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
    "read_file": ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file from the server's /app directory (relative path).",
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
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
    "chat": ToolSpec(
        name="chat",
        description="Call an OpenAI Responses API compatible endpoint.",
        input_schema=ToolSchema(
            properties={
                "model": {"type": "string", "description": "Model name, e.g. gpt-4.1-mini"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "description": "system|user|assistant|tool"},
                            "content": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "object"}},
                                ]
                            },
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                    "description": "Conversation history in OpenAI chat format.",
                },
                "temperature": {"type": "number", "description": "0-2 range", "default": 0.7},
                "max_tokens": {"type": "integer", "description": "Max output tokens for the response"},
                "top_p": {"type": "number", "description": "Nucleus sampling"},
                "tools": {
                    "type": "array",
                    "description": "Hosted tools for Responses API (e.g., [{'type':'web_search'}]).",
                    "items": {"type": "object"},
                },
                "tool_choice": {
                    "type": "string",
                    "description": "Tool choice mode for Responses API (e.g., 'auto').",
                },
                "metadata": {"type": "object", "description": "Optional vendor-specific options"},
                "parallelToolCalls": {
                    "type": "boolean",
                    "description": "Allow hosted tools to run in parallel",
                },
            },
            required=["model", "messages"],
        ),
        output_schema=ToolSchema(
            properties={
                "content": {"type": "array"},
                "toolCalls": {"type": "array"},
                "isError": {"type": "boolean"},
            },
        ),
    ),
}

__all__ = [
    "ToolHandler",
    "ToolResponse",
    "ToolSchema",
    "ToolSpec",
    "TOOLS",
]
