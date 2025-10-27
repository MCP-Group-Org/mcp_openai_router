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
    def __init__(self, payload: Dict[str, Any] | list[Dict[str, Any]]) -> None:
        if isinstance(payload, list):
            self._payloads = payload
        else:
            self._payloads = [payload]
        self._call_index = 0
        self.last_request: Dict[str, Any] = {}
        self.requests: list[Dict[str, Any]] = []

    def _select_payload(self) -> Dict[str, Any]:
        if self._call_index < len(self._payloads):
            return self._payloads[self._call_index]
        return self._payloads[-1]

    def create(self, **kwargs: Any) -> DummyResponse:
        self.last_request = kwargs
        self.requests.append(kwargs)
        payload = self._select_payload()
        self._call_index += 1
        return DummyResponse(payload)

    def retrieve(self, response_id: str | None = None, id: str | None = None) -> DummyResponse:
        index = 0 if self._call_index == 0 else min(self._call_index - 1, len(self._payloads) - 1)
        return DummyResponse(self._payloads[index])


class DummyOpenAIClient:
    def __init__(self, payload: Dict[str, Any] | list[Dict[str, Any]]) -> None:
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


def test_chat_handles_think_function_call(monkeypatch: pytest.MonkeyPatch) -> None:
    think_call_id = "call_think_42"
    initial_payload = {
        "id": "resp_init",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "function_call",
                        "id": "fc_stub_1",
                        "call_id": think_call_id,
                        "name": "think",
                        "arguments": '{"thought": "Найди инсайты", "parent_trace_id": "trace-123"}',
                    }
                ],
            }
        ],
    }
    final_payload = {
        "id": "resp_final",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Финальный ответ."},
                ],
            }
        ],
    }
    dummy_client = DummyOpenAIClient([initial_payload, final_payload])
    monkeypatch.setattr(mcp, "_create_openai_client", lambda: dummy_client)

    def fake_handle_think(arguments: Dict[str, Any]) -> Dict[str, Any]:
        assert arguments["thought"] == "Найди инсайты"
        return mcp._tool_ok(
            content=[
                {"type": "text", "text": "Первый блок"},
                {"type": "text", "text": "Второй блок"},
            ],
            metadata={"stub": True},
        )

    monkeypatch.setattr(mcp, "_handle_think", fake_handle_think)
    monkeypatch.setattr(mcp, "THINK_TOOL_CONFIG", mcp.ThinkToolConfig(enabled=True))

    client = TestClient(mcp.app)

    init_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "pytest", "version": "1.0"},
                "capabilities": {},
            },
        },
    )
    assert init_response.status_code == 200
    session_id = init_response.json()["result"]["sessionId"]

    call_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "sessionId": session_id,
                "name": "chat",
                "arguments": {
                    "model": "gpt-think",
                    "messages": [
                        {"role": "system", "content": "Test harness."},
                        {"role": "user", "content": "Запусти think."},
                    ],
                },
            },
        },
    )
    assert call_response.status_code == 200
    payload = call_response.json()["result"]
    assert payload["isError"] is False
    assert payload["content"][0]["text"] == "Финальный ответ."
    assert payload["toolCalls"] == []
    assert payload["metadata"]["thinkTool"][0]["callId"] == think_call_id
    assert payload["metadata"]["thinkTool"][0]["status"] == "ok"

    requests = dummy_client.responses.requests
    assert len(requests) == 2
    first_request = requests[0]
    follow_up_request = requests[1]
    assert first_request["model"] == "gpt-think"
    assert follow_up_request["previous_response_id"] == "resp_init"
    follow_up_input = follow_up_request["input"][0]
    assert follow_up_input["type"] == "function_call_output"
    assert follow_up_input["call_id"] == think_call_id
    assert follow_up_input["output"][0]["type"] == "input_text"
    assert follow_up_input["output"][0]["text"] == "Первый блок\n\nВторой блок"
