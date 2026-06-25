"""Data-source adapters.

Each adapter is a thin, defensive wrapper around one provider (AkShare, FRED,
Finnhub). Adapters never raise to the caller for *data* problems — they return
``None`` for a missing datum so the tool layer can assemble a ``partial`` /
``unavailable`` envelope. They only raise for *programmer* errors.

Defensiveness matters because we cannot pin the exact upstream JSON/HTML shape
(AkShare scrapes endpoints that change); fuzzy column matching keeps a schema
drift from crashing the whole tool — it just degrades that one field to null.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


def to_float(x: Any) -> Optional[float]:
    """Best-effort float coercion; returns None for blanks / dashes / junk."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        f = float(x)
        return None if f != f else f  # drop NaN
    s = str(x).strip().replace(",", "").replace("%", "").replace("+", "")
    if s in ("", "-", "--", "—", "None", "nan", "NaN", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick_col(df, candidates: Iterable[str]):
    """Return the first DataFrame column whose name contains any candidate.

    Matches on substring (case-insensitive) so "成交额(元)" matches "成交额".
    """
    cols = list(df.columns)
    low = {c: str(c).lower() for c in cols}
    for cand in candidates:
        cl = cand.lower()
        for c in cols:
            if cl in low[c]:
                return c
    return None

def last_row(df):
    """Last row of a DataFrame as a plain dict, or None if empty."""
    if df is None or len(df) == 0:
        return None
    return df.iloc[-1].to_dict()
