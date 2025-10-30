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


def test_chat_returns_error_when_responses_api_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenClient:
        pass

    monkeypatch.setattr(mcp, "_create_openai_client", lambda: BrokenClient())

    result = mcp._handle_chat(
        {
            "model": "gpt-error",
            "messages": [
                {"role": "system", "content": "Test"},
                {"role": "user", "content": "Hello"},
            ],
        }
    )

    assert result["isError"] is True
    assert "Responses API" in result["content"][0]["text"]


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
        # Проверяем, что передаются метаданные LangSmith для связывания трассировки
        # (они автоматически добавляются в main.py перед вызовом think_processor)
        metadata = arguments.get("metadata")
        if metadata:
            langsmith_meta = metadata.get("langsmith")
            # Если есть активный tracer, должны быть parent_run_id
            if langsmith_meta:
                assert "parent_run_id" in langsmith_meta or "trace_id" in langsmith_meta
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


def test_chat_returns_error_with_think_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    think_call_id = "call_think_error"
    payload = {
        "id": "resp_error",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "function_call",
                        "id": "fc_err",
                        "call_id": think_call_id,
                        "name": "think",
                        "arguments": '{"thought": "fail"}',
                    }
                ],
            }
        ],
    }
    dummy_client = DummyOpenAIClient(payload)
    monkeypatch.setattr(mcp, "_create_openai_client", lambda: dummy_client)

    def fake_handle_think(arguments: Dict[str, Any]) -> Dict[str, Any]:
        return mcp._tool_error("think failed", metadata={"reason": "mock"})

    monkeypatch.setattr(mcp, "_handle_think", fake_handle_think)
    monkeypatch.setattr(mcp, "THINK_TOOL_CONFIG", mcp.ThinkToolConfig(enabled=True))

    result = mcp._handle_chat(
        {
            "model": "gpt-think",
            "messages": [
                {"role": "system", "content": "Test harness."},
                {"role": "user", "content": "Запусти think."},
            ],
        }
    )

    assert result["isError"] is True
    error_text = result["content"][0]["text"]
    assert "think failed" in error_text
    metadata = result.get("metadata") or {}
    think_logs = metadata.get("thinkTool") or []
    assert think_logs[0]["callId"] == think_call_id
    assert think_logs[0]["status"] == "error"
    assert metadata.get("reason") == "mock"


def test_chat_records_langsmith_run(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "resp_ls",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Ответ с трассировкой."},
                ],
            }
        ],
    }
    dummy_client = DummyOpenAIClient(payload)
    monkeypatch.setattr(mcp, "_create_openai_client", lambda: dummy_client)

    import app.services.langsmith_tracing as tracing

    class DummyLangSmithClient:
        def __init__(self) -> None:
            self.created = []
            self.updated = []

        def create_run(self, **kwargs: Any) -> str:
            self.created.append(kwargs)
            return kwargs.get("id") or "run-stub"

        def update_run(self, run_id: str, **kwargs: Any) -> None:
            self.updated.append((run_id, kwargs))

    stub_client = DummyLangSmithClient()
    monkeypatch.setattr(tracing, "_CLIENT_CACHE", None)
    monkeypatch.setattr(tracing, "_CLIENT_FAILED", False)
    monkeypatch.setattr(tracing, "_get_langsmith_client", lambda: stub_client)

    client = TestClient(mcp.app)

    init_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 20,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "pytest", "version": "1.0"},
                "capabilities": {},
            },
        },
    )
    assert init_response.status_code == 200
    session_id = init_response.json()["result"]["sessionId"]

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "sessionId": session_id,
                "name": "chat",
                "arguments": {
                    "model": "gpt-langsmith",
                    "messages": [
                        {"role": "user", "content": "Включи трассу."},
                    ],
                    "metadata": {
                        "langsmith": {
                            "parent_run_id": "parent-001",
                            "trace_id": "trace-xyz",
                            "project": "proj-test",
                            "tags": ["unit-test"],
                        }
                    },
                },
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False

    langsmith_meta = (result.get("metadata") or {}).get("langsmith") or {}
    assert langsmith_meta.get("parentRunId") == "parent-001"
    assert langsmith_meta.get("project") == "proj-test"
    assert langsmith_meta.get("traceId") == "trace-xyz"

    assert stub_client.created, "LangSmith run was not created"
    created_kwargs = stub_client.created[0]
    run_id = created_kwargs.get("id")
    assert created_kwargs.get("parent_run_id") == "parent-001"
    # trace_id не передаётся при наличии parent_run_id (наследуется автоматически)
    assert "trace_id" not in created_kwargs
    assert created_kwargs.get("project_name") == "proj-test"
    assert created_kwargs.get("tags") == ["unit-test"]
    assert created_kwargs.get("run_type") == "llm"  # LLM run для корректного отображения

    assert stub_client.updated, "LangSmith run was not finalized"
    updated_run_id, update_payload = stub_client.updated[0]
    assert updated_run_id == run_id
    outputs = update_payload.get("outputs", {})
    assert "response" in outputs
    # Проверяем, что есть текстовый output для UI LangSmith
    assert "output" in outputs
    assert "Ответ с трассировкой." in outputs["output"]
