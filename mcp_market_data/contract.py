"""Output contract shared by every tool.

Contract rules (from the build spec, section 4):

- Every tool returns the same envelope: ``as_of_local`` (GMT+8),
  ``as_of_source_tz`` (source timezone), ``session_date``, ``source``,
  ``data``, ``status`` (ok | partial | unavailable), ``notes``.
- A field that cannot be fetched is ``null`` — never a placeholder number,
  never a fabricated value. Consensus values in particular: ``null`` when the
  source does not return them.
- Numbers carry a unit (``bp`` / ``%`` / ``pts`` / ``CNY`` ...).
- Timestamps are given in BOTH GMT+8 and the source timezone.
- Internal computation is UTC; output is converted on the way out.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

GMT8 = ZoneInfo("Asia/Shanghai")  # GMT+8, abbreviates as CST via %Z

# Status values
OK = "ok"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"


def q(value: Any, unit: str) -> dict[str, Any]:
    """A single quantity: a number (or ``None`` when missing) plus its unit.

    Using ``{value, unit}`` keeps the payload machine-readable while honouring
    the spec's "值 + 单位" requirement. ``value`` is ``None`` when the datum
    could not be fetched — the morning-report prompt renders that as ``—``.
    """
    return {"value": value, "unit": unit}


def _fmt_source_tz(dt_utc: datetime, source_tz: Optional[str]) -> str:
    """Render the as-of time in the source's own timezone, with its abbrev."""
    if not source_tz:
        local = dt_utc.astimezone(GMT8)
        return local.strftime("%Y-%m-%dT%H:%M:%S ") + local.strftime("%Z")
    src = dt_utc.astimezone(ZoneInfo(source_tz))
    return src.strftime("%Y-%m-%dT%H:%M:%S ") + src.strftime("%Z")


def envelope(
    *,
    source: str,
    data: dict[str, Any],
    status: str = OK,
    notes: str = "",
    source_tz: Optional[str] = None,
    as_of_utc: Optional[datetime] = None,
    session_date: Optional[str] = None,
) -> dict[str, Any]:
    """Build a contract-compliant envelope.

    ``source_tz`` is an IANA name (e.g. ``"America/New_York"``,
    ``"Asia/Shanghai"``). When omitted the source tz equals GMT+8.
    """
    now = as_of_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(GMT8)
    return {
        "as_of_local": local.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "as_of_source_tz": _fmt_source_tz(now, source_tz),
        "session_date": session_date or local.date().isoformat(),
        "source": source,
        "data": data,
        "status": status,
        "notes": notes,
    }


def unavailable(source: str, notes: str, *, source_tz: Optional[str] = None) -> dict[str, Any]:
    """Shorthand for a whole-tool failure: empty data, status unavailable."""
    return envelope(
        source=source,
        data={},
        status=UNAVAILABLE,
        notes=notes,
        source_tz=source_tz,
    )


def derive_status(data: dict[str, Any]) -> str:
    """Infer ok / partial / unavailable from how many leaf quantities are set.

    A leaf is a ``{value, unit}`` quantity. ``ok`` = every leaf has a value;
    ``partial`` = some do; ``unavailable`` = none do (or there are no leaves).
    """
    leaves = list(_iter_quantities(data))
    if not leaves:
        return UNAVAILABLE
    have = sum(1 for leaf in leaves if leaf.get("value") is not None)
    if have == 0:
        return UNAVAILABLE
    if have == len(leaves):
        return OK
    return PARTIAL


def _iter_quantities(obj: Any):
    """Yield every ``{value, unit}`` dict reachable in a nested structure."""
    if isinstance(obj, dict):
        if set(obj.keys()) == {"value", "unit"}:
            yield obj
            return
        for v in obj.values():
            yield from _iter_quantities(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_quantities(v)
