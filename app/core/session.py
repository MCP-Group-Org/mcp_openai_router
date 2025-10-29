"""Хранилище и утилиты для управления сессиями MCP."""

from __future__ import annotations

from typing import Dict

from app.models.json_rpc import SessionState

ACTIVE_SESSIONS: Dict[str, SessionState] = {}

__all__ = ["ACTIVE_SESSIONS"]
