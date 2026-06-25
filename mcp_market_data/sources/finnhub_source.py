"""Finnhub adapter — US equity quotes, VIX, sector ETFs, FX spot, oil, calendar.

Key: ``FINNHUB_API_KEY`` (env). Free tier ~60 req/min and limited symbol
coverage — known gaps are handled by the tool layer (ETF proxies for indices,
``null`` consensus for the economic calendar). Source timezone: quote
timestamps are epoch UTC; for US equities we report the source tz as US
Eastern.
"""

from __future__ import annotations

import os
from typing import Any, Optional

SOURCE = "Finnhub"
US_SOURCE_TZ = "America/New_York"

_client = None


def _finnhub():
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise RuntimeError("FINNHUB_API_KEY not set")
    import finnhub

    _client = finnhub.Client(api_key=key)
    return _client


def quote(symbol: str) -> Optional[dict[str, Any]]:
    """Return a normalized quote, or ``None`` when the symbol is uncovered.

    Finnhub ``quote`` returns ``{c,d,dp,h,l,o,pc,t}``. On the free tier an
    uncovered symbol comes back all-zero (``c==0 and pc==0``) — we treat that
    as "no data" rather than a real price of zero.
    """
    data = _finnhub().quote(symbol)
    if not data:
        return None
    c = data.get("c")
    pc = data.get("pc")
    if (c in (0, None)) and (pc in (0, None)):
        return None
    return {
        "current": c,
        "change": data.get("d"),
        "change_pct": data.get("dp"),
        "high": data.get("h"),
        "low": data.get("l"),
        "open": data.get("o"),
        "prev_close": pc,
        "epoch": data.get("t"),
    }


def safe_quote(symbol: str) -> Optional[dict[str, Any]]:
    try:
        return quote(symbol)
    except Exception:
        return None


def economic_calendar() -> Optional[list[dict[str, Any]]]:
    """Economic calendar events. Often paywalled on the free tier.

    Returns the raw event list or ``None``. The tool layer maps missing
    consensus to ``null`` and NEVER fabricates a consensus value.
    """
    data = _finnhub().economic_calendar()
    if not data:
        return None
    return data.get("economicCalendar") if isinstance(data, dict) else data
