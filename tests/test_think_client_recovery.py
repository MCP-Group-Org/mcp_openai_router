"""Тесты автоматического восстановления сессии think-tool после рестарта сервиса."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.think_client import ThinkCallResult, ThinkToolClient, ThinkToolConfig


class MockResponse:
    """Мок httpx.Response для тестирования."""

    def __init__(self, status_code: int, text: str = "", headers: Dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8") if text else b""
        self.headers = headers or {}

    def json(self) -> Dict[str, Any]:
        import json

        return json.loads(self.text) if self.text else {}


@pytest.fixture
def think_config() -> ThinkToolConfig:
    """Конфигурация для тестового клиента think-tool."""
    return ThinkToolConfig(
        enabled=True,
        url="http://test-think-tool:8080/mcp",
        timeout_ms=2000,
        retry_limit=0,
    )


def test_automatic_session_recovery_on_400(think_config: ThinkToolConfig) -> None:
    """Тест автоматического восстановления сессии при получении 400 с ошибкой о невалидной сессии.

    Сценарий:
    1. Клиент инициализирован с сессией "old-session-123"
    2. Первый вызов tools/call возвращает 400 "No valid session ID provided"
    3. Клиент автоматически сбрасывает сессию и выполняет handshake
    4. Повторный вызов tools/call успешен
    5. Результат: успешный вызов capture_thought(), новый sessionId
    """
    with patch("app.think_client.httpx") as mock_httpx:
        # Настройка мока httpx.Client
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client

        call_count = {"count": 0}
        old_session_id = "old-session-123"

        def mock_post(url: str, json: Dict[str, Any], headers: Dict[str, str]) -> MockResponse:
            call_count["count"] += 1
            method = json.get("method")

            # Первый tools/call с устаревшей сессией → 400
            if call_count["count"] == 1 and method == "tools/call":
                return MockResponse(
                    status_code=400,
                    text='{"error": "Bad Request: No valid session ID provided"}',
                )

            # После reset: handshake ping → новая сессия
            if method == "ping":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {}}',
                    headers={"mcp-session-id": "new-session-456"},
                )

            # initialize
            if method == "initialize":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {"protocolVersion": "1.0"}}',
                )

            # notification
            if method == "notifications/initialized":
                return MockResponse(status_code=200, text='{"jsonrpc": "2.0"}')

            # Повторный tools/call с новой сессией → успех
            if method == "tools/call":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "ok"}]}}',
                )

            return MockResponse(status_code=500, text='{"error": "unexpected"}')

        mock_client.post.side_effect = mock_post

        # Создаём клиент и инициализируем с "старой" сессией
        client = ThinkToolClient(think_config)
        client._session_id = old_session_id
        client._initialized = True

        # Вызываем capture_thought - должно произойти автоматическое восстановление
        result = client.capture_thought("test thought")

        # Проверки
        assert result.ok is True, f"Ожидался успешный результат, но получена ошибка: {result.error}"
        assert client._session_id == "new-session-456", (
            f"Session ID должен измениться на новый, "
            f"но остался: {client._session_id}"
        )
        assert client._session_id != old_session_id, "Session ID не должен остаться старым"

        # Проверяем, что было несколько вызовов (handshake + retry)
        assert call_count["count"] >= 4, (
            f"Ожидалось минимум 4 вызова (400 + ping + initialize + notification + tools/call), "
            f"но было: {call_count['count']}"
        )


def test_session_recovery_fails_on_second_attempt(think_config: ThinkToolConfig) -> None:
    """Тест ограничения retry: если повторная попытка тоже возвращает ошибку сессии - возвращается ошибка.

    Сценарий:
    1. Первый tools/call → 400 с ошибкой сессии
    2. Reset и handshake
    3. Повторный tools/call → снова 400 с ошибкой сессии
    4. Результат: ошибка (нет бесконечного цикла, только 1 retry)
    """
    with patch("app.think_client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client

        def mock_post(url: str, json: Dict[str, Any], headers: Dict[str, str]) -> MockResponse:
            method = json.get("method")

            # tools/call всегда возвращает 400 (эмуляция нестабильного сервиса)
            if method == "tools/call":
                return MockResponse(
                    status_code=400,
                    text='{"error": "Bad Request: No valid session ID provided"}',
                )

            # Handshake проходит успешно
            if method == "ping":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {}}',
                    headers={"mcp-session-id": "session-retry-456"},
                )
            if method == "initialize":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {"protocolVersion": "1.0"}}',
                )
            if method == "notifications/initialized":
                return MockResponse(status_code=200, text='{"jsonrpc": "2.0"}')

            return MockResponse(status_code=500, text='{"error": "unexpected"}')

        mock_client.post.side_effect = mock_post

        client = ThinkToolClient(think_config)
        client._session_id = "old-session-777"
        client._initialized = True

        result = client.capture_thought("test thought")

        # Должна быть ошибка (не бесконечный цикл)
        assert result.ok is False, "Ожидалась ошибка после неудачного retry"
        assert "восстановить сессию" in result.error.lower(), (
            f"Ошибка должна упоминать неудачу восстановления сессии, "
            f"но текст: {result.error}"
        )


def test_session_state_reset_on_400(think_config: ThinkToolConfig) -> None:
    """Тест проверки вызова метода _reset_session() при обнаружении ошибки сессии.

    Сценарий:
    1. Первый tools/call → 400 с ошибкой сессии
    2. Проверяем, что _reset_session() был вызван
    3. После успешного retry _session_id НЕ None (новая сессия создана)
    """
    with patch("app.think_client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client

        call_count = {"count": 0}

        def mock_post(url: str, json: Dict[str, Any], headers: Dict[str, str]) -> MockResponse:
            call_count["count"] += 1
            method = json.get("method")

            # Первый tools/call → 400
            if call_count["count"] == 1 and method == "tools/call":
                return MockResponse(
                    status_code=400,
                    text='{"error": "Bad Request: No valid session ID provided"}',
                )

            # Handshake после reset
            if method == "ping":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {}}',
                    headers={"mcp-session-id": "reset-session-999"},
                )
            if method == "initialize":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {"protocolVersion": "1.0"}}',
                )
            if method == "notifications/initialized":
                return MockResponse(status_code=200, text='{"jsonrpc": "2.0"}')

            # Повторный tools/call → успех
            if method == "tools/call":
                return MockResponse(
                    status_code=200,
                    text='{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "ok"}]}}',
                )

            return MockResponse(status_code=500, text='{"error": "unexpected"}')

        mock_client.post.side_effect = mock_post

        client = ThinkToolClient(think_config)
        client._session_id = "old-session-888"
        client._initialized = True

        # Используем spy для отслеживания вызова _reset_session
        original_reset = client._reset_session
        reset_called = {"called": False}

        def spy_reset_session():
            reset_called["called"] = True
            original_reset()

        client._reset_session = spy_reset_session  # type: ignore[assignment]

        result = client.capture_thought("test thought")

        # Проверки
        assert result.ok is True, f"Ожидался успешный результат: {result.error}"
        assert reset_called["called"] is True, "_reset_session() должен был быть вызван"
        assert client._session_id is not None, (
            "_session_id должен быть заполнен после retry (новая сессия создана)"
        )
        assert client._session_id == "reset-session-999", (
            f"_session_id должен быть новым после восстановления, "
            f"но: {client._session_id}"
        )


def test_no_retry_on_other_400_errors(think_config: ThinkToolConfig) -> None:
    """Тест: retry НЕ выполняется для других 400 ошибок (не связанных с сессией).

    Сценарий:
    1. tools/call → 400 "Bad Request: Invalid JSON-RPC" (НЕ про сессию)
    2. Проверяем, что retry НЕ произошел
    3. Проверяем, что _reset_session() НЕ вызывался
    """
    with patch("app.think_client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value = mock_client

        call_count = {"count": 0}

        def mock_post(url: str, json: Dict[str, Any], headers: Dict[str, str]) -> MockResponse:
            call_count["count"] += 1
            method = json.get("method")

            # tools/call → 400 с ДРУГОЙ ошибкой (не про сессию)
            if method == "tools/call":
                return MockResponse(
                    status_code=400,
                    text='{"error": "Bad Request: Invalid JSON-RPC request"}',
                )

            # Handshake НЕ должен вызываться
            return MockResponse(status_code=500, text='{"error": "should not be called"}')

        mock_client.post.side_effect = mock_post

        client = ThinkToolClient(think_config)
        client._session_id = "session-no-retry"
        client._initialized = True

        # Spy для _reset_session
        reset_called = {"called": False}
        original_reset = client._reset_session

        def spy_reset_session():
            reset_called["called"] = True
            original_reset()

        client._reset_session = spy_reset_session  # type: ignore[assignment]

        result = client.capture_thought("test thought")

        # Проверки
        assert result.ok is False, "Должна быть ошибка для невалидного JSON-RPC"
        assert reset_called["called"] is False, (
            "_reset_session() НЕ должен вызываться для не-сессионных ошибок"
        )
        # Только один вызов tools/call, никакого handshake
        assert call_count["count"] == 1, (
            f"Должен быть только 1 вызов (без retry), но было: {call_count['count']}"
        )
        assert "Invalid JSON-RPC" in result.error, (
            f"Ошибка должна содержать оригинальный текст, но: {result.error}"
        )
