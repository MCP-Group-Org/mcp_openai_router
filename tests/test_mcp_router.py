from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import app.main as mcp


class DummyResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> Dict[str, Any]:
        return self._payload


class DummyResponsesAPI:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.last_request: Dict[str, Any] = {}

    def create_and_poll(self, **kwargs: Any) -> DummyResponse:
        self.last_request = kwargs
        return DummyResponse(self._payload)


class DummyOpenAIClient:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.responses = DummyResponsesAPI(payload)


@pytest.fixture(autouse=True)
def clear_sessions() -> None:
    mcp.ACTIVE_SESSIONS.clear()


def test_initialize_list_and_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: fake OpenAI Responses payload containing both text and tool call.
    payload = {
        "id": "resp_123",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello from Responses API."},
                    {
                        "type": "tool_call",
                        "id": "tool_1",
                        "name": "read_file",
                        "arguments": {"path": "notes.md"},
                    },
                ],
            }
        ],
        "usage": {"input_tokens": 12, "output_tokens": 24, "total_tokens": 36},
    }
    dummy_client = DummyOpenAIClient(payload)
    monkeypatch.setattr(mcp, "_create_openai_client", lambda: dummy_client)

    client = TestClient(mcp.app)

    # Act 1: initialize handshake
    init_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "pytest", "version": "1.0"},
                "capabilities": {},
            },
        },
    )
    assert init_response.status_code == 200
    init_payload = init_response.json()
    assert init_payload["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    session_id = init_payload["result"]["sessionId"]

    # Act 2: tools/list
    list_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"sessionId": session_id},
        },
    )
    assert list_response.status_code == 200
    tools_payload = list_response.json()["result"]["tools"]
    tool_names = sorted(tool["name"] for tool in tools_payload)
    assert "chat" in tool_names
    chat_tool = next(tool for tool in tools_payload if tool["name"] == "chat")
    assert "inputSchema" in chat_tool
    assert chat_tool["inputSchema"]["type"] == "object"

    # Act 3: tools/call(chat)
    call_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "sessionId": session_id,
                "name": "chat",
                "arguments": {
                    "model": "gpt-test",
                    "messages": [
                        {"role": "system", "content": "You are a test harness."},
                        {"role": "user", "content": "Ping"},
                    ],
                    "temperature": 0,
                },
            },
        },
    )
    assert call_response.status_code == 200
    result = call_response.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "Hello from Responses API."
    assert result["toolCalls"][0]["toolName"] == "read_file"
    assert dummy_client.responses.last_request["model"] == "gpt-test"
    assert dummy_client.responses.last_request["input"][0]["role"] == "system"
