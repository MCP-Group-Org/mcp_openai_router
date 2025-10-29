"""Опциональная интеграция с LangSmith для трассировки вызовов MCP."""

from __future__ import annotations

import copy
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

# SDK может отсутствовать в окружении — обрабатываем мягко.
try:  # pragma: no cover - ветка зависит от внешней зависимости
    from langsmith import Client  # type: ignore
except Exception:  # pragma: no cover - минимизируем влияние на основной код
    Client = None  # type: ignore[assignment]

logger = logging.getLogger("mcp_openai_router.services.langsmith_tracing")


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _coerce_tags(raw: Any) -> List[str]:
    result: List[str] = []
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)
            elif isinstance(item, (int, float, bool)):
                result.append(str(item))
    return result


def _coerce_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    return {}


@dataclass(slots=True)
class LangSmithSettings:
    """Глобальная конфигурация LangSmith из окружения."""

    enabled: bool = False
    project: Optional[str] = None

    @classmethod
    def from_env(cls) -> "LangSmithSettings":
        enabled = _truthy(os.getenv("LANGSMITH_TRACING"))
        project = _coerce_str(os.getenv("LANGSMITH_PROJECT")) or None
        return cls(enabled=enabled, project=project)


@dataclass(slots=True)
class LangSmithContext:
    """Контекст трассировки, передаваемый из клиента через metadata."""

    parent_run_id: Optional[str] = None
    trace_id: Optional[str] = None
    run_id: Optional[str] = None
    project: Optional[str] = None
    run_name: str = "mcp_openai_router.chat"
    run_type: str = "tool"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    force_enable: bool = False

    def should_activate(self) -> bool:
        return self.force_enable or any([self.parent_run_id, self.run_id, self.trace_id])


def _extract_context(raw_metadata: Optional[Dict[str, Any]]) -> LangSmithContext:
    context = LangSmithContext()
    if not isinstance(raw_metadata, dict):
        return context

    nested = raw_metadata.get("langsmith")
    nested_dict = nested if isinstance(nested, dict) else {}

    context.parent_run_id = (
        _coerce_str(nested_dict.get("parent_run_id"))
        or _coerce_str(raw_metadata.get("langsmith_parent_run_id"))
    )
    context.trace_id = (
        _coerce_str(nested_dict.get("trace_id"))
        or _coerce_str(raw_metadata.get("langsmith_trace_id"))
    )
    context.run_id = (
        _coerce_str(nested_dict.get("run_id"))
        or _coerce_str(raw_metadata.get("langsmith_run_id"))
    )
    context.project = (
        _coerce_str(nested_dict.get("project"))
        or _coerce_str(raw_metadata.get("langsmith_project"))
    )
    context.run_name = _coerce_str(nested_dict.get("name")) or context.run_name
    context.run_type = _coerce_str(nested_dict.get("run_type")) or context.run_type
    context.tags = _coerce_tags(nested_dict.get("tags"))
    context.metadata = _coerce_metadata(nested_dict.get("metadata"))
    context.force_enable = bool(nested_dict.get("enabled") is True)

    return context


_CLIENT_CACHE: Optional[Any] = None
_CLIENT_FAILED: bool = False


def _get_langsmith_client() -> Optional[Any]:
    global _CLIENT_CACHE, _CLIENT_FAILED
    if _CLIENT_FAILED:
        return None
    if _CLIENT_CACHE is not None:
        return _CLIENT_CACHE
    if Client is None:  # pragma: no cover - отсутствие SDK
        _CLIENT_FAILED = True
        return None
    try:
        _CLIENT_CACHE = Client()
    except Exception as exc:  # pragma: no cover - сеть/конфигурация
        logger.warning("LangSmith client init failed: %s", exc)
        _CLIENT_FAILED = True
        return None
    return _CLIENT_CACHE


class LangSmithTracer:
    """Утилита для создания и завершения LangSmith-run вокруг вызова chat."""

    def __init__(
        self,
        settings: LangSmithSettings,
        context: LangSmithContext,
        client: Optional[Any],
    ) -> None:
        self._settings = settings
        self.context = context
        self._client = client
        self._active = bool(client) and (settings.enabled or context.should_activate())
        self._started = False
        self._closed = False
        self.project_name = context.project or settings.project or "mcp_openai_router"
        self.run_id: Optional[str] = context.run_id
        self.trace_id: Optional[str] = context.trace_id
        self._start_time: Optional[datetime] = None

    def start(self, inputs: Dict[str, Any]) -> None:
        if not self._active or self._started or not self._client:
            return
        self._started = True
        self._start_time = datetime.now(timezone.utc)

        if self.run_id is None:
            self.run_id = str(uuid.uuid4())
        if self.trace_id is None and not self.context.parent_run_id:
            self.trace_id = str(uuid.uuid4())

        create_kwargs: Dict[str, Any] = {
            "name": self.context.run_name,
            "inputs": inputs,
            "run_type": self.context.run_type,
            "id": self.run_id,
        }
        if self.project_name:
            create_kwargs["project_name"] = self.project_name
        if self.context.parent_run_id:
            create_kwargs["parent_run_id"] = self.context.parent_run_id
        if self.trace_id:
            create_kwargs["trace_id"] = self.trace_id
        if self.context.tags:
            create_kwargs["tags"] = self.context.tags
        if self.context.metadata:
            create_kwargs["metadata"] = self.context.metadata

        try:
            self._client.create_run(**create_kwargs)
        except Exception as exc:  # pragma: no cover - внешняя зависимость
            logger.warning("LangSmith create_run failed: %s", exc)
            self._active = False
            self.run_id = None
            self.trace_id = None

    def _update_run(self, *, outputs: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        if not self._active or not self._client or not self.run_id or self._closed:
            return
        payload: Dict[str, Any] = {}
        if outputs is not None:
            payload["outputs"] = outputs
        if error is not None:
            payload["error"] = error
        payload["end_time"] = datetime.now(timezone.utc)

        try:
            self._client.update_run(self.run_id, **payload)
        except Exception as exc:  # pragma: no cover - внешняя зависимость
            logger.warning("LangSmith update_run failed: %s", exc)
        finally:
            self._closed = True
            self._active = False

    def attach_to_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        if not self.run_id:
            return response
        metadata = response.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            response["metadata"] = metadata
        langsmith_meta = metadata.get("langsmith")
        if not isinstance(langsmith_meta, dict):
            langsmith_meta = {}
            metadata["langsmith"] = langsmith_meta

        langsmith_meta.setdefault("runId", self.run_id)
        if self.trace_id:
            langsmith_meta.setdefault("traceId", self.trace_id)
        if self.project_name:
            langsmith_meta.setdefault("project", self.project_name)
        if self.context.parent_run_id:
            langsmith_meta.setdefault("parentRunId", self.context.parent_run_id)
        if self.context.tags:
            langsmith_meta.setdefault("tags", list(self.context.tags))
        langsmith_meta.setdefault("runName", self.context.run_name)
        langsmith_meta.setdefault("runType", self.context.run_type)
        return response

    def finalize_success(self, response: Dict[str, Any]) -> Dict[str, Any]:
        response = self.attach_to_response(response)
        self._update_run(outputs={"response": response})
        return response

    def finalize_error(self, response: Dict[str, Any], *, message: str) -> Dict[str, Any]:
        response = self.attach_to_response(response)
        self._update_run(outputs={"response": response}, error=message)
        return response


def create_langsmith_tracer(raw_metadata: Optional[Dict[str, Any]]) -> LangSmithTracer:
    settings = LangSmithSettings.from_env()
    context = _extract_context(raw_metadata)
    client: Optional[Any] = None
    if settings.enabled or context.should_activate():
        client = _get_langsmith_client()
    tracer = LangSmithTracer(settings=settings, context=context, client=client)
    return tracer


__all__ = ["LangSmithTracer", "create_langsmith_tracer"]
