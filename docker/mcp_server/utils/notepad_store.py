# mcp_server/utils/notepad_store.py
"""Very small in-memory key/value scratchpad.

Swap for Redis if REDIS_URL env var is set.
"""
from __future__ import annotations

import os
from typing import Any
import redis

if os.getenv("REDIS_URL"):
    _r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)  # type: ignore
else:
    _r = None  # in-process fallback

_STORE: dict[str, str] = {}


def write(key: str, value: str) -> None:
    if _r:
        _r.set(key, value, ex=3600)  # expire in 1 h
    else:
        _STORE[key] = value[:1024]  # 1 KB cap


def read(key: str) -> str | None:
    if _r:
        v = _r.get(key)
        return str(v) if v is not None else None
    return _STORE.get(key)


def clear(key: str) -> None:
    if _r:
        _r.delete(key)
    else:
        _STORE.pop(key, None)
