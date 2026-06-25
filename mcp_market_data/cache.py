"""Lightweight per-session-date cache.

The morning report runs once per day. Hitting AkShare / FRED / Finnhub for the
same ``session_date`` repeatedly wastes rate budget (Finnhub free tier is
~60 req/min; FRED is generous but polite), so a tool result is cached under
``(tool_name, session_date)`` for the rest of that local (GMT+8) day.

Cache is intentionally in-memory only — the server is a single always-on
process (see deploy notes; pitfall #1 in the playbook is *exactly* about not
running multiple instances), so a process-local dict is sufficient and avoids
the file-lock contention that hung the oura server.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable

from .contract import GMT8, UNAVAILABLE

_lock = threading.Lock()
_store: dict[tuple[str, str], dict[str, Any]] = {}


def _today_session_date() -> str:
    return datetime.now(GMT8).date().isoformat()


def cached(tool_name: str, producer: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Return a cached envelope for today's session, or produce + store one.

    ``unavailable`` results are NOT cached — a transient source outage should
    not poison the whole day; the next call retries.
    """
    session_date = _today_session_date()
    key = (tool_name, session_date)
    with _lock:
        hit = _store.get(key)
    if hit is not None:
        return hit
    result = producer()
    if result.get("status") != UNAVAILABLE:
        with _lock:
            _store[key] = result
    return result


def clear() -> None:
    with _lock:
        _store.clear()
