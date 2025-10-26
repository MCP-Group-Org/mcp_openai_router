"""Клиент для взаимодействия с удалённым think-tool (сервер MCP)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

try:
    import httpx
except Exception:  # pragma: no cover - graceful degradation, обработается при создании клиента
    httpx = None  # type: ignore[assignment]


logger = logging.getLogger("mcp_openai_router.think_client")

_SESSION_HEADER = "mcp-session-id"


def _get_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class ThinkToolConfig:
    """Настройки клиента think-tool, получаемые из окружения."""

    enabled: bool = False
    url: Optional[str] = None
    timeout_ms: int = 2000
    retry_limit: int = 0

    @classmethod
    def from_env(cls) -> "ThinkToolConfig":
        enabled = _get_bool(os.getenv("THINK_TOOL_ENABLED"))
        url = os.getenv("THINK_TOOL_URL")
        timeout_raw = os.getenv("THINK_TOOL_TIMEOUT_MS")
        retry_raw = os.getenv("THINK_TOOL_RETRY_LIMIT")

        timeout_ms = 2000
        if timeout_raw:
            try:
                timeout_ms = max(0, int(timeout_raw))
            except ValueError:
                logger.warning("Некорректное значение THINK_TOOL_TIMEOUT_MS=%r, используем 2000", timeout_raw)

        retry_limit = 0
        if retry_raw:
            try:
                retry_limit = max(0, int(retry_raw))
            except ValueError:
                logger.warning("Некорректное значение THINK_TOOL_RETRY_LIMIT=%r, используем 0", retry_raw)

        return cls(
            enabled=enabled,
            url=url,
            timeout_ms=timeout_ms,
            retry_limit=retry_limit,
        )


@dataclass(slots=True)
class ThinkCallResult:
    """Результат обращения к think-tool."""

    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    was_skipped: bool = False

    @classmethod
    def skipped(cls, reason: str) -> "ThinkCallResult":
        return cls(ok=False, result=None, error=reason, was_skipped=True)


class ThinkToolClient:
    """Минимальный JSON-RPC клиент для удалённого think-tool."""

    def __init__(self, config: ThinkToolConfig):
        if httpx is None:
            raise RuntimeError("httpx недоступен, think-tool клиент не может быть создан.")
        if not config.url:
            raise ValueError("THINK_TOOL_URL не задан — think-tool клиент не может быть создан.")

        self._config = config
        self._session_id: Optional[str] = None
        self._initialized = False
        self._client = httpx.Client(timeout=(config.timeout_ms / 1000) or None)

    def close(self) -> None:
        self._client.close()

    def capture_thought(self, thought: str, parent_trace_id: Optional[str] = None) -> ThinkCallResult:
        """Отправить мысль в think-tool. Возвращает нормализованный ответ."""
        print("[debug] capture_thought: enabled=", self._config.enabled, "url=", self._config.url)
        print("[debug] capture_thought: thought=", thought)
        print("[debug] capture_thought: parent_trace_id=", parent_trace_id)
        if not self._config.enabled:
            print("[debug] capture_thought: skipped because disabled config")
            return ThinkCallResult.skipped("think-tool отключён конфигурацией.")
        if not thought.strip():
            print("[debug] capture_thought: skipped because empty thought")
            return ThinkCallResult.skipped("передана пустая мысль.")

        try:
            print("[debug] capture_thought: ensuring initialized, current session:", self._session_id)
            self._ensure_initialized()
            print("[debug] capture_thought: ensure_initialized done, session:", self._session_id)
        except Exception as exc:  # pragma: no cover - реальная сеть недоступна в тестах
            logger.warning("Не удалось выполнить handshake с think-tool: %s", exc)
            print("[debug] capture_thought: ensure_initialized exception", exc)
            return ThinkCallResult(ok=False, error=str(exc))

        payload = {
            "jsonrpc": "2.0",
            "id": f"think-{uuid4().hex}",
            "method": "tools/call",
            "params": {
                "name": "think",
                "arguments": {
                    "thought": thought,
                    "parent_trace_id": parent_trace_id,
                },
                "stream": False,
            },
        }

        try:
            print("[debug] capture_thought: sending payload", payload)
            response, status_code = self._post(payload, allow_error=False)
            print("[debug] capture_thought: response status", status_code, "body", response)
        except Exception as exc:  # pragma: no cover - проксируется в лог, в тестах отрабатываем моком
            logger.warning("Ошибка обращения к think-tool: %s", exc)
            print("[debug] capture_thought: _post exception", exc)
            return ThinkCallResult(ok=False, error=str(exc))

        if not response:
            print("[debug] capture_thought: empty response from think-tool")
            return ThinkCallResult(ok=False, error="пустой ответ от think-tool", status_code=status_code)

        error_obj = response.get("error")
        if error_obj:
            message = error_obj.get("message") or "think-tool вернул ошибку"
            print("[debug] capture_thought: error object", error_obj)
            return ThinkCallResult(ok=False, error=message, status_code=status_code, result=error_obj)

        print("[debug] capture_thought: success result", response.get("result"))
        return ThinkCallResult(
            ok=True,
            result=response.get("result"),
            status_code=status_code,
        )

    # --- внутренние методы ---

    def _ensure_initialized(self) -> None:
        if self._initialized:
            print("[debug] _ensure_initialized: already initialized with session", self._session_id)
            return
        print("[debug] _ensure_initialized: start")
        self._ensure_session()
        self._send_initialize()
        self._send_initialized_notification()
        self._initialized = True
        print("[debug] _ensure_initialized: done with session", self._session_id)

    def _ensure_session(self) -> None:
        if self._session_id:
            print("[debug] _ensure_session: existing session", self._session_id)
            return

        print("[debug] _ensure_session: requesting session via ping")
        payload = {
            "jsonrpc": "2.0",
            "id": f"ping-{uuid4().hex}",
            "method": "ping",
            "params": {},
        }

        response, _ = self._post(payload, include_session=False, allow_error=True)
        print("[debug] _ensure_session: ping response", response)
        session_id = None
        if response and isinstance(response, dict):
            # Некоторые реализации могут возвращать sessionId в результате.
            session_id = response.get("result", {}).get("sessionId") or response.get("sessionId")
        if not session_id:
            session_id = self._session_id

        if not session_id:
            print("[debug] _ensure_session: server did not provide session id")
            raise RuntimeError("Сервер think-tool не вернул session ID.")

        self._session_id = session_id
        print("[debug] _ensure_session: new session", self._session_id)

    def _send_initialize(self) -> None:
        print("[debug] _send_initialize: session", self._session_id)
        payload = {
            "jsonrpc": "2.0",
            "id": f"init-{uuid4().hex}",
            "method": "initialize",
            "params": {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "mcp-openai-router", "version": "0.0.2"},
                "capabilities": {},
            },
        }
        response, status = self._post(payload, allow_error=False)
        print("[debug] _send_initialize: status", status, "response", response)

    def _send_initialized_notification(self) -> None:
        print("[debug] _send_initialized_notification: session", self._session_id)
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        response, status = self._post(payload, allow_error=False)
        print("[debug] _send_initialized_notification: status", status, "response", response)

    def _post(
        self,
        payload: Dict[str, Any],
        *,
        include_session: bool = True,
        allow_error: bool = False,
    ) -> tuple[Dict[str, Any], int]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_session and self._session_id:
            headers[_SESSION_HEADER] = self._session_id

        last_exc: Optional[Exception] = None

        attempts = max(1, self._config.retry_limit + 1)

        for _ in range(attempts):
            try:
                print("[debug] _post: sending", payload, "headers", headers)
                response = self._client.post(self._config.url, json=payload, headers=headers)
            except Exception as exc:  # pragma: no cover - реальный сетевой сбой
                last_exc = exc
                print("[debug] _post: exception", exc)
                continue

            session_id = response.headers.get(_SESSION_HEADER)
            if session_id:
                self._session_id = session_id
                print("[debug] _post: updated session from headers", self._session_id)

            if not allow_error and response.status_code >= 400:
                # Собираем тело для диагностики.
                parsed = self._parse_response(response)
                detail = parsed.get("error") if isinstance(parsed, dict) else parsed
                print("[debug] _post: error status", response.status_code, "parsed", parsed)
                raise RuntimeError(f"think-tool вернул {response.status_code}: {detail}")

            parsed = self._parse_response(response)
            print("[debug] _post: parsed response", parsed)
            return parsed, response.status_code

        if last_exc:
            print("[debug] _post: raising last exception", last_exc)
            raise last_exc

        raise RuntimeError("Неизвестная ошибка think-tool (нет ответа).")

    @staticmethod
    def _parse_response(response: "httpx.Response") -> Dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()

        if "text/event-stream" in content_type:
            payload: Dict[str, Any] = {}
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if not data_str:
                    continue
                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    payload = {"raw": data_str}
                # SSE может содержать несколько событий, но для MCP нас интересует последнее.
            return payload

        if not response.content:
            return {}

        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": response.text}


def create_think_tool_client(config: ThinkToolConfig) -> Optional[ThinkToolClient]:
    """Создать клиент с учётом конфигурации; вернуть None, если инструмент отключён."""
    if not config.enabled:
        logger.info("think-tool отключён конфигурацией.")
        return None

    if httpx is None:
        logger.warning("httpx не установлен — think-tool будет отключён.")
        return None

    url = (config.url or "").strip()
    if not url:
        logger.warning("THINK_TOOL_URL не задан — think-tool будет отключён.")
        return None

    try:
        return ThinkToolClient(config)
    except Exception as exc:
        logger.warning("Не удалось создать клиент think-tool: %s", exc)
        return None
