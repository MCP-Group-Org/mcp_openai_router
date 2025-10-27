"""Глобальные константы и настройки приложения MCP OpenAI Router."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Semaphore
from typing import Dict, Optional

from app.think_client import ThinkToolClient, ThinkToolConfig, create_think_tool_client

PROTOCOL_VERSION = "1.0"
SERVER_INFO: Dict[str, str] = {
    "name": "mcp-openai-router",
    "version": os.getenv("APP_VERSION", "0.0.2"),
}
SERVER_CAPABILITIES: Dict[str, Dict[str, object]] = {
    "tools": {
        "listChangedNotification": False,
        "parallelCalls": True,
    },
    "sampling": {
        "supportsHostedTools": True,
    },
}

ENABLE_LEGACY_METHODS = (
    "--legacy" in sys.argv
    or os.getenv("MCP_ENABLE_LEGACY", "").lower() in {"1", "true", "yes"}
)

BASE_DIR = Path("/app").resolve()
REQUIRE_SESSION = os.getenv("MCP_REQUIRE_SESSION", "1").strip().lower() in {"1", "true", "yes", "on"}

RESPONSES_POLL_MAX_CONCURRENCY = int(os.getenv("RESPONSES_POLL_MAX_CONCURRENCY", "8"))
POLL_SEM = Semaphore(max(1, RESPONSES_POLL_MAX_CONCURRENCY))

THINK_TOOL_CONFIG = ThinkToolConfig.from_env()
THINK_TOOL_CLIENT: Optional[ThinkToolClient] = create_think_tool_client(THINK_TOOL_CONFIG)

__all__ = [
    "BASE_DIR",
    "ENABLE_LEGACY_METHODS",
    "POLL_SEM",
    "PROTOCOL_VERSION",
    "REQUIRE_SESSION",
    "RESPONSES_POLL_MAX_CONCURRENCY",
    "SERVER_CAPABILITIES",
    "SERVER_INFO",
    "THINK_TOOL_CLIENT",
    "THINK_TOOL_CONFIG",
]
