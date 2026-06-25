"""FRED adapter — US Treasury yields, Fed policy rate, macro series.

Key: ``FRED_API_KEY`` (env). Free, generous rate limits.
Source timezone: FRED publishes daily series stamped in US Eastern.
"""

from __future__ import annotations

import os
from typing import Any, Optional

SOURCE = "FRED (St. Louis Fed)"
SOURCE_TZ = "America/New_York"

_client = None


def _fred():
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set")
    from fredapi import Fred

    _client = Fred(api_key=key)
    return _client


def latest(series_id: str) -> Optional[tuple[float, str]]:
    """Most recent non-null observation of a FRED series.

    Returns ``(value, observation_date_iso)`` or ``None``. Never raises for a
    data gap — only for a missing key / transport failure (caller catches).
    """
    s = _fred().get_series(series_id)
    s = s.dropna()
    if s.empty:
        return None
    val = float(s.iloc[-1])
    date = s.index[-1].date().isoformat()
    return val, date


def latest_value(series_id: str) -> Optional[float]:
    got = latest(series_id)
    return None if got is None else got[0]


def fetch_many(series_ids: dict[str, str]) -> dict[str, Any]:
    """Fetch a batch of series. Returns ``{alias: (value, date) | None}``.

    Each series is isolated: one failure does not abort the rest.
    """
    out: dict[str, Any] = {}
    for alias, sid in series_ids.items():
        try:
            out[alias] = latest(sid)
        except Exception:
            out[alias] = None
    return out
