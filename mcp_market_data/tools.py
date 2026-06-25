"""The 8 morning-report tools.

Each ``get_*`` function returns a contract envelope (see ``contract.py``). They
are plain functions so they can be unit-tested without an MCP transport;
``server.py`` registers them as MCP tools.

Design invariants:
- Every external call is wrapped so one source outage degrades a section to
  ``unavailable`` (or a field to ``null``) but never aborts another section.
- Missing data is ``null`` + ``status`` downgrade — never a fabricated number.
- The economic calendar NEVER invents a consensus: no consensus -> ``null``.
"""

from __future__ import annotations

import logging
from typing import Any

from . import contract as C
from .cache import cached
from .contract import OK, PARTIAL, UNAVAILABLE, derive_status, envelope, q, unavailable
from .sources import akshare_source as ak_src
from .sources import finnhub_source as fh
from .sources import fred_source as fred

log = logging.getLogger("market_data_mcp.tools")

CST_TZ = "Asia/Shanghai"
ET_TZ = "America/New_York"

# US sector ETFs (SPDR) -> sector label, used to derive leaders/laggards when
# Finnhub free tier lacks the underlying S&P sector indices.
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Svcs",
}


# --------------------------------------------------------------------------- #
# 1. US equities
# --------------------------------------------------------------------------- #
def get_us_equities() -> dict[str, Any]:
    """S&P 500 / Nasdaq / Dow / Russell 2000 close + intraday%, VIX, and S&P
    sector leaders/laggards. Uses Finnhub; falls back to ETF proxies for the
    indices when the raw index symbols are uncovered (free-tier gap)."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        try:
            indices = {
                "sp500": ("^GSPC", "SPY"),
                "nasdaq": ("^IXIC", "QQQ"),
                "dow": ("^DJI", "DIA"),
                "russell2000": ("^RUT", "IWM"),
            }
            data: dict[str, Any] = {}
            used_proxy = False
            for key, (raw, proxy) in indices.items():
                qd = fh.safe_quote(raw)
                src_label = "index"
                if qd is None:
                    qd = fh.safe_quote(proxy)
                    src_label = f"ETF proxy ({proxy})"
                    if qd is not None:
                        used_proxy = True
                if qd is None:
                    data[key] = {"close": q(None, "pts"), "change_pct": q(None, "%"), "via": None}
                else:
                    data[key] = {
                        "close": q(qd["current"], "pts"),
                        "change_pct": q(qd["change_pct"], "%"),
                        "via": src_label,
                    }

            vix = fh.safe_quote("^VIX")
            data["vix"] = q(vix["current"] if vix else None, "pts")

            # Sector leaders/laggards via SPDR sector ETFs.
            sectors = []
            for sym, label in SECTOR_ETFS.items():
                sq = fh.safe_quote(sym)
                if sq and sq.get("change_pct") is not None:
                    sectors.append({"sector": label, "etf": sym, "change_pct": sq["change_pct"]})
            if sectors:
                sectors.sort(key=lambda s: s["change_pct"], reverse=True)
                data["sector_leaders"] = [
                    {"sector": s["sector"], "etf": s["etf"], "change_pct": q(s["change_pct"], "%")}
                    for s in sectors[:3]
                ]
                data["sector_laggards"] = [
                    {"sector": s["sector"], "etf": s["etf"], "change_pct": q(s["change_pct"], "%")}
                    for s in sectors[-3:]
                ]
                notes.append("Sector leaders/laggards derived from SPDR sector ETFs.")
            else:
                data["sector_leaders"] = None
                data["sector_laggards"] = None

            if used_proxy:
                notes.append("Some indices use ETF proxies (SPY/QQQ/DIA/IWM) — Finnhub free-tier index gap.")
            if vix is None:
                notes.append("VIX unavailable on this Finnhub tier.")

            return envelope(
                source="Finnhub",
                data=data,
                status=derive_status(data),
                notes=" ".join(notes),
                source_tz=ET_TZ,
            )
        except Exception as e:  # pragma: no cover - whole-source failure
            log.exception("get_us_equities failed")
            return unavailable("Finnhub", f"US equities source error: {e}", source_tz=ET_TZ)

    return cached("get_us_equities", build)


# --------------------------------------------------------------------------- #
# 2. US rates
# --------------------------------------------------------------------------- #
def get_us_rates() -> dict[str, Any]:
    """UST 10Y / 2Y (FRED), 10s2s spread, Fed funds target band (FRED), and DXY
    (best-effort via Finnhub; marked unavailable rather than guessed)."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        data: dict[str, Any] = {}
        session_date = None
        try:
            series = fred.fetch_many(
                {
                    "dgs10": "DGS10",
                    "dgs2": "DGS2",
                    "fed_upper": "DFEDTARU",
                    "fed_lower": "DFEDTARL",
                }
            )
            d10 = series["dgs10"]
            d2 = series["dgs2"]
            up = series["fed_upper"]
            lo = series["fed_lower"]
            y10 = d10[0] if d10 else None
            y2 = d2[0] if d2 else None
            if d10:
                session_date = d10[1]
            data["ust_10y"] = q(y10, "%")
            data["ust_2y"] = q(y2, "%")
            data["spread_10s2s"] = q(
                round((y10 - y2) * 100, 1) if (y10 is not None and y2 is not None) else None, "bp"
            )
            data["fed_target_upper"] = q(up[0] if up else None, "%")
            data["fed_target_lower"] = q(lo[0] if lo else None, "%")
        except Exception as e:
            log.exception("FRED block failed in get_us_rates")
            notes.append(f"FRED unavailable: {e}")
            for k in ("ust_10y", "ust_2y", "fed_target_upper", "fed_target_lower"):
                data.setdefault(k, q(None, "%"))
            data.setdefault("spread_10s2s", q(None, "bp"))

        # DXY: best-effort. Finnhub free tier usually lacks it -> null, not guessed.
        dxy = fh.safe_quote("DXY")
        data["dxy"] = q(dxy["current"] if dxy else None, "index")
        if dxy is None:
            notes.append("DXY unavailable on Finnhub free tier (not fabricated).")

        status = derive_status(data)
        # If FRED itself died entirely, surface unavailable.
        if data["ust_10y"]["value"] is None and data["ust_2y"]["value"] is None:
            status = UNAVAILABLE
        return envelope(
            source="FRED (St. Louis Fed) + Finnhub (DXY best-effort)",
            data=data,
            status=status,
            notes=" ".join(notes),
            source_tz=ET_TZ,
            session_date=session_date,
        )

    return cached("get_us_rates", build)


# --------------------------------------------------------------------------- #
# 3. USD/CNY spot
# --------------------------------------------------------------------------- #
def get_usdcny_spot() -> dict[str, Any]:
    """PBOC central parity, onshore CFETS spot (bid/ask/mid), offshore USDCNH
    (Finnhub), and the CNH-CNY basis."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        data: dict[str, Any] = {}
        session_date = None

        try:
            mid = ak_src.pboc_midpoint("美元")
            data["pboc_midpoint"] = q(mid["value"], "USD/CNY")
            session_date = mid.get("date") or session_date
        except Exception as e:
            data["pboc_midpoint"] = q(None, "USD/CNY")
            notes.append(f"PBOC midpoint unavailable: {e}")

        onshore_mid = None
        try:
            spot = ak_src.cfets_spot("USD/CNY")
            onshore_mid = spot["mid"]
            data["onshore_spot"] = {
                "bid": q(spot["bid"], "USD/CNY"),
                "ask": q(spot["ask"], "USD/CNY"),
                "mid": q(spot["mid"], "USD/CNY"),
            }
        except Exception as e:
            data["onshore_spot"] = {
                "bid": q(None, "USD/CNY"),
                "ask": q(None, "USD/CNY"),
                "mid": q(None, "USD/CNY"),
            }
            notes.append(f"CFETS onshore spot unavailable: {e}")

        cnh = fh.safe_quote("OANDA:USD_CNH")
        cnh_val = cnh["current"] if cnh else None
        data["offshore_usdcnh"] = q(cnh_val, "USD/CNH")
        if cnh is None:
            notes.append("USDCNH unavailable on Finnhub (not fabricated).")

        # CNH-CNY basis (offshore minus onshore mid), in pips.
        if cnh_val is not None and onshore_mid is not None:
            data["cnh_cny_basis"] = q(round((cnh_val - onshore_mid) * 10000, 0), "pips")
        else:
            data["cnh_cny_basis"] = q(None, "pips")

        notes.append("Northbound real-time flow is not published anymore — intentionally omitted.")
        return envelope(
            source="AkShare / SAFE / CFETS + Finnhub (USDCNH)",
            data=data,
            status=derive_status(data),
            notes=" ".join(notes),
            source_tz=CST_TZ,
            session_date=session_date,
        )

    return cached("get_usdcny_spot", build)


# --------------------------------------------------------------------------- #
# 4. USD/CNY forwards
# --------------------------------------------------------------------------- #
def get_usdcny_forwards() -> dict[str, Any]:
    """CFETS USD/CNY swap points for 1M / 3M / 1Y. Marked unavailable when the
    CFETS swap feed cannot be read."""

    def build() -> dict[str, Any]:
        try:
            sp = ak_src.fx_swap_points("USD/CNY")
            data = {
                "swap_1m": q(sp["1M"], "pips"),
                "swap_3m": q(sp["3M"], "pips"),
                "swap_1y": q(sp["1Y"], "pips"),
            }
            return envelope(
                source="AkShare / CFETS",
                data=data,
                status=derive_status(data),
                notes="Day-on-day swap-point change requires a prior snapshot; not stored in this stateless build.",
                source_tz=CST_TZ,
            )
        except Exception as e:
            return unavailable("AkShare / CFETS", f"USD/CNY swap points unavailable: {e}", source_tz=CST_TZ)

    return cached("get_usdcny_forwards", build)


# --------------------------------------------------------------------------- #
# 5. China rates
# --------------------------------------------------------------------------- #
def get_cn_rates() -> dict[str, Any]:
    """DR007 / R007 (CFETS fixing proxies), SHIBOR O/N & 1W, 10Y / 1Y CGB, and
    1Y / 5Y LPR."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        data: dict[str, Any] = {}
        session_date = None

        try:
            repo = ak_src.repo_rates()
            data["dr007"] = q(repo["dr007_proxy"], "%")
            data["r007"] = q(repo["r007_proxy"], "%")
            session_date = repo.get("date") or session_date
            if repo.get("proxy"):
                notes.append("DR007/R007 use CFETS fixing repo (FDR007/FR007) as proxies — verify口径 on deploy.")
        except Exception as e:
            data["dr007"] = q(None, "%")
            data["r007"] = q(None, "%")
            notes.append(f"Repo rates unavailable: {e}")

        try:
            sh = ak_src.shibor()
            data["shibor_on"] = q(sh["on"], "%")
            data["shibor_1w"] = q(sh["1w"], "%")
        except Exception as e:
            data["shibor_on"] = q(None, "%")
            data["shibor_1w"] = q(None, "%")
            notes.append(f"SHIBOR unavailable: {e}")

        try:
            cgb = ak_src.cgb_yield()
            data["cgb_10y"] = q(cgb["y10"], "%")
            data["cgb_1y"] = q(cgb["y1"], "%")
            session_date = cgb.get("date") or session_date
        except Exception as e:
            data["cgb_10y"] = q(None, "%")
            data["cgb_1y"] = q(None, "%")
            notes.append(f"CGB yield unavailable: {e}")

        try:
            lpr = ak_src.lpr()
            data["lpr_1y"] = q(lpr["y1"], "%")
            data["lpr_5y"] = q(lpr["y5"], "%")
        except Exception as e:
            data["lpr_1y"] = q(None, "%")
            data["lpr_5y"] = q(None, "%")
            notes.append(f"LPR unavailable: {e}")

        return envelope(
            source="AkShare / CFETS / ChinaBond / PBOC",
            data=data,
            status=derive_status(data),
            notes=" ".join(notes),
            source_tz=CST_TZ,
            session_date=session_date,
        )

    return cached("get_cn_rates", build)


# --------------------------------------------------------------------------- #
# 6. China equities
# --------------------------------------------------------------------------- #
def get_cn_equities() -> dict[str, Any]:
    """SSE Comp / SZSE Comp / CSI 300 / ChiNext / STAR 50 close, change%,
    turnover (亿元), market breadth, and Shenwan level-1 sector moves."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        data: dict[str, Any] = {}
        session_date = None

        indices: dict[str, Any] = {}
        for name, code in ak_src.CN_INDEX.items():
            try:
                d = ak_src.index_daily(code)
                indices[name] = {
                    "close": q(d["close"], "pts"),
                    "change_pct": q(d["change_pct"], "%"),
                    "turnover": q(d["amount_yi"], "亿元"),
                }
                session_date = d.get("date") or session_date
            except Exception as e:
                indices[name] = {
                    "close": q(None, "pts"),
                    "change_pct": q(None, "%"),
                    "turnover": q(None, "亿元"),
                }
                notes.append(f"{name} unavailable: {e}")
        data["indices"] = indices

        try:
            b = ak_src.market_breadth()
            data["breadth"] = {
                "advancers": q(b["advancers"], "count"),
                "decliners": q(b["decliners"], "count"),
                "unchanged": q(b["unchanged"], "count"),
                "limit_up": q(b["limit_up"], "count"),
                "limit_down": q(b["limit_down"], "count"),
            }
        except Exception as e:
            data["breadth"] = None
            notes.append(f"Market breadth unavailable: {e}")

        try:
            sectors = ak_src.sw_first_sectors()
            sectors = [s for s in sectors if s.get("change_pct") is not None]
            if sectors:
                sectors.sort(key=lambda s: s["change_pct"], reverse=True)
                data["sw_l1_leaders"] = [
                    {"name": s["name"], "change_pct": q(s["change_pct"], "%")} for s in sectors[:5]
                ]
                data["sw_l1_laggards"] = [
                    {"name": s["name"], "change_pct": q(s["change_pct"], "%")} for s in sectors[-5:]
                ]
            else:
                data["sw_l1_leaders"] = None
                data["sw_l1_laggards"] = None
                notes.append("Shenwan L1 sector moves unavailable.")
        except Exception as e:
            data["sw_l1_leaders"] = None
            data["sw_l1_laggards"] = None
            notes.append(f"Shenwan L1 sectors unavailable: {e}")

        return envelope(
            source="AkShare (eastmoney / 乐咕 / Shenwan)",
            data=data,
            status=derive_status(data),
            notes=" ".join(notes),
            source_tz=CST_TZ,
            session_date=session_date,
        )

    return cached("get_cn_equities", build)


# --------------------------------------------------------------------------- #
# 7. Commodities
# --------------------------------------------------------------------------- #
def get_commodities() -> dict[str, Any]:
    """SHFE gold/silver/copper/zinc/nickel (AkShare) + Brent/WTI (Finnhub,
    best-effort) close and change%."""

    def build() -> dict[str, Any]:
        notes: list[str] = []
        data: dict[str, Any] = {}
        session_date = None

        metals: dict[str, Any] = {}
        for name, sym in ak_src.SHFE_MAIN.items():
            try:
                d = ak_src.shfe_main(sym)
                metals[name] = {
                    "close": q(d["close"], "CNY"),
                    "change_pct": q(d["change_pct"], "%"),
                }
                session_date = d.get("date") or session_date
            except Exception as e:
                metals[name] = {"close": q(None, "CNY"), "change_pct": q(None, "%")}
                notes.append(f"SHFE {name} unavailable: {e}")
        data["shfe_metals"] = metals

        # Brent / WTI best-effort via Finnhub.
        brent = fh.safe_quote("OANDA:BCO_USD")
        wti = fh.safe_quote("OANDA:WTICO_USD")
        data["brent"] = {
            "close": q(brent["current"] if brent else None, "USD/bbl"),
            "change_pct": q(brent["change_pct"] if brent else None, "%"),
        }
        data["wti"] = {
            "close": q(wti["current"] if wti else None, "USD/bbl"),
            "change_pct": q(wti["change_pct"] if wti else None, "%"),
        }
        if brent is None or wti is None:
            notes.append("Brent/WTI best-effort via Finnhub; null when uncovered (not fabricated).")

        return envelope(
            source="AkShare / SHFE + Finnhub (oil)",
            data=data,
            status=derive_status(data),
            notes=" ".join(notes),
            source_tz=CST_TZ,
            session_date=session_date,
        )

    return cached("get_commodities", build)


# --------------------------------------------------------------------------- #
# 8. Economic calendar
# --------------------------------------------------------------------------- #
def get_econ_calendar() -> dict[str, Any]:
    """Today / overnight economic releases: actual / consensus / prior
    (Finnhub, best-effort). Consensus is NEVER fabricated — ``null`` when the
    feed does not return it (Finnhub calendar is often paywalled)."""

    def build() -> dict[str, Any]:
        from datetime import datetime, timedelta

        try:
            events = fh.economic_calendar()
        except Exception as e:
            return unavailable(
                "Finnhub (economic calendar)",
                f"Economic calendar unavailable (often paywalled): {e}. Morning report shows — for consensus.",
                source_tz=ET_TZ,
            )

        if not events:
            return envelope(
                source="Finnhub (economic calendar)",
                data={"events": []},
                status=UNAVAILABLE,
                notes="Economic calendar empty/paywalled on this tier; consensus left as null — never fabricated.",
                source_tz=ET_TZ,
            )

        today = datetime.utcnow().date()
        window = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        rows = []
        for ev in events:
            d = str(ev.get("time", ev.get("date", "")))[:10]
            if window and d and d not in window:
                continue
            rows.append(
                {
                    "event": ev.get("event"),
                    "country": ev.get("country"),
                    "time": ev.get("time"),
                    "actual": q(ev.get("actual"), ev.get("unit", "")),
                    # Consensus stays exactly as the feed gives it — null if absent.
                    "consensus": q(ev.get("estimate"), ev.get("unit", "")),
                    "prior": q(ev.get("prev"), ev.get("unit", "")),
                }
            )

        return envelope(
            source="Finnhub (economic calendar)",
            data={"events": rows},
            status=OK if rows else PARTIAL,
            notes="Consensus = feed's estimate field verbatim; null when absent — never fabricated.",
            source_tz=ET_TZ,
        )

    return cached("get_econ_calendar", build)


# Registry used by server.py and tests.
ALL_TOOLS = {
    "get_us_equities": get_us_equities,
    "get_us_rates": get_us_rates,
    "get_usdcny_spot": get_usdcny_spot,
    "get_usdcny_forwards": get_usdcny_forwards,
    "get_cn_rates": get_cn_rates,
    "get_cn_equities": get_cn_equities,
    "get_commodities": get_commodities,
    "get_econ_calendar": get_econ_calendar,
}
