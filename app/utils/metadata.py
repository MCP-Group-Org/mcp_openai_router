"""Утилиты для сериализации/десериализации метаданных для транспорта через OpenAI API."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def serialise_metadata_for_openai(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Возвращает копию metadata с сериализованными полями для OpenAI.

    Сейчас сериализуем только ключ `langsmith`: если он dict/list, превращаем в JSON-строку.
    Другие ключи остаются как есть.
    """
    if not isinstance(metadata, dict):
        return metadata
    new_md: Dict[str, Any] = dict(metadata)
    ls = new_md.get("langsmith")
    if isinstance(ls, (dict, list)):
        try:
            new_md["langsmith"] = json.dumps(ls, ensure_ascii=False)
        except Exception:
            # не прерываем отправку, оставляем как есть
            pass
    return new_md


def deserialise_metadata_from_openai(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Возвращает копию metadata с десериализованным `langsmith`, если это строка JSON."""
    if not isinstance(metadata, dict):
        return {}
    new_md: Dict[str, Any] = dict(metadata)
    ls = new_md.get("langsmith")
    if isinstance(ls, str):
        try:
            new_md["langsmith"] = json.loads(ls)
        except json.JSONDecodeError:
            # оставляем оригинальную строку при некорректном JSON
            pass
    return new_md


__all__ = [
    "serialise_metadata_for_openai",
    "deserialise_metadata_from_openai",
]
