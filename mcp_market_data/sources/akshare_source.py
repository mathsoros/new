"""AkShare adapter — all China data + SHFE commodities.

No API key required. Source timezone: Asia/Shanghai (CST, GMT+8), so for China
data ``as_of_local`` and ``as_of_source_tz`` differ only in label.

Every function calls a single, named AkShare endpoint and parses its result
with fuzzy column matching (``pick_col``) so that an upstream schema drift
degrades one field to ``None`` rather than crashing the tool. Functions may
raise on a hard transport failure — the tool layer catches and marks the
section ``unavailable``.

NOTE for the deployment host: AkShare scrapes Chinese financial endpoints
(eastmoney / sina / CFETS). They are reachable from the Windows always-on box
but were blocked by the build sandbox's egress proxy, so the live smoke tests
(acceptance criteria #3) must be run on the deployment host.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from . import last_row, pick_col, to_float
from ..contract import GMT8

# Index code -> human name (used by get_cn_equities)
CN_INDEX = {
    "上证综指": "000001",
    "深证成指": "399001",
    "沪深300": "000300",
    "创业板指": "399006",
    "科创50": "000688",
}

# SHFE main-continuous contract symbols (Sina) -> human name
SHFE_MAIN = {
    "黄金": "AU0",
    "白银": "AG0",
    "铜": "CU0",
    "锌": "ZN0",
    "镍": "NI0",
}


def _ak():
    import akshare as ak

    return ak


def _window(days: int = 20) -> tuple[str, str]:
    today = datetime.now(GMT8).date()
    start = today - timedelta(days=days)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _retry(fn, attempts: int = 3, base: float = 0.8):
    """Call ``fn`` with small backoff retries.

    eastmoney's push2 endpoints intermittently close the connection without a
    response (``RemoteDisconnected``) when hit in rapid succession — a couple of
    spaced retries clears it. Raises the last exception if all attempts fail.
    """
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised after retries
            last = e
            time.sleep(base * (i + 1))
    raise last


def _pair_row(df, pair_c, *, offshore: bool = False):
    """Find the USD/CNY (or USD/CNH) row regardless of how the pair is spelled.

    CFETS frames label the pair as "USD/CNY", "美元/人民币", "USDCNY" etc.;
    match on the presence of both legs instead of an exact string.
    """
    s = df[pair_c].astype(str)
    usd = s.str.contains("USD") | s.str.contains("美元")
    if offshore:
        cny = s.str.contains("CNH") | s.str.contains("离岸")
    else:
        cny = (s.str.contains("CNY") | s.str.contains("人民币")) & ~s.str.contains("CNH")
    return last_row(df[usd & cny])


def _parse_swap_cell(x) -> Optional[float]:
    """Parse a CFETS swap-points cell into a single midpoint number.

    Cells are "bid/ask" strings, e.g. "-143.84/-132.00" -> -137.92; a one-sided
    "---/-1770.00" -> -1770.0; "---/---" -> None.
    """
    if x is None:
        return None
    parts = [to_float(p) for p in str(x).split("/")]
    vals = [v for v in parts if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


# --------------------------------------------------------------------------- #
# Equities
# --------------------------------------------------------------------------- #
def _parse_index_hist(df) -> Optional[dict[str, Optional[float]]]:
    """Parse an A-share index daily frame (收盘/涨跌幅/成交额/日期 columns)."""
    row = last_row(df)
    if row is None:
        return None
    close_c = pick_col(df, ["收盘"])
    chg_c = pick_col(df, ["涨跌幅"])
    amt_c = pick_col(df, ["成交额", "amount"])
    date_c = pick_col(df, ["日期", "date"])
    close = to_float(row.get(close_c)) if close_c else None
    if close is None:
        return None
    chg = to_float(row.get(chg_c)) if chg_c else None
    # Some sources (stock_zh_index_daily_em) carry no 涨跌幅 → derive from prev close.
    if chg is None and close_c and len(df) >= 2:
        prev = to_float(df.iloc[-2].get(close_c))
        if prev:
            chg = round((close - prev) / prev * 100, 2)
    amount = to_float(row.get(amt_c)) if amt_c else None
    return {
        "close": close,
        "change_pct": chg,
        # 成交额 in CNY → report in 亿元 (1e8), the morning-report unit.
        "amount_yi": round(amount / 1e8, 2) if amount is not None else None,
        "date": str(row.get(date_c)) if date_c else None,
    }


# Sina serves the whole index board in one direct call; memoize it briefly so
# the 5-index loop doesn't refetch the 500+ row snapshot each time.
_SINA_SPOT: dict[str, Any] = {"df": None, "ts": 0.0}


def _sina_code(code: str) -> str:
    """A-share index code -> Sina symbol (sh/sz prefix)."""
    return ("sh" if code[0] == "0" else "sz") + code


def _sina_index_spot():
    now = time.time()
    if _SINA_SPOT["df"] is not None and now - _SINA_SPOT["ts"] < 120:
        return _SINA_SPOT["df"]
    df = _retry(lambda: _ak().stock_zh_index_spot_sina())
    _SINA_SPOT["df"] = df
    _SINA_SPOT["ts"] = now
    return df


def index_daily(code: str) -> dict[str, Optional[float]]:
    """Latest close / change% / turnover for an A-share index.

    Primary: ``stock_zh_index_spot_sina`` — Sina's index snapshot reaches the
    deploy host on the DIRECT China route (eastmoney push2his gets connection-
    reset there). When the market is closed the snapshot's 最新价 is the last
    close, which is what the morning report wants. Fallback: ``index_zh_a_hist``
    (eastmoney; only reachable via proxy).
    """
    empty = {"close": None, "change_pct": None, "amount_yi": None, "date": None}
    sina_code = _sina_code(code)
    try:
        df = _sina_index_spot()
        code_c = pick_col(df, ["代码", "symbol", "code"]) or df.columns[0]
        sub = df[df[code_c].astype(str).str.lower() == sina_code]
        row = last_row(sub)
        if row is not None:
            close_c = pick_col(df, ["最新价", "收盘", "close", "price"])
            chg_c = pick_col(df, ["涨跌幅", "changepercent"])
            amt_c = pick_col(df, ["成交额", "amount"])
            close = to_float(row.get(close_c)) if close_c else None
            if close is not None:
                amount = to_float(row.get(amt_c)) if amt_c else None
                return {
                    "close": close,
                    "change_pct": to_float(row.get(chg_c)) if chg_c else None,
                    "amount_yi": round(amount / 1e8, 2) if amount is not None else None,
                    "date": datetime.now(GMT8).date().isoformat(),
                }
    except Exception:
        pass
    # Fallback: eastmoney daily hist (reachable only via proxy on this host).
    start, end = _window()
    try:
        df = _retry(
            lambda: _ak().index_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end
            )
        )
        parsed = _parse_index_hist(df)
        if parsed:
            return parsed
    except Exception:
        pass
    return empty


def market_breadth() -> dict[str, Optional[float]]:
    """Advancers / decliners / unchanged across the whole A-share market.

    ``stock_market_activity_legu`` returns item/value rows (上涨/下跌/平盘/...).
    """
    df = _ak().stock_market_activity_legu()
    item_c = pick_col(df, ["item", "项目", "名称"]) or df.columns[0]
    val_c = pick_col(df, ["value", "值", "数量"]) or df.columns[-1]
    m = {str(r[item_c]).strip(): to_float(r[val_c]) for _, r in df.iterrows()}

    def g(*names):
        for n in names:
            for k, v in m.items():
                if n in k:
                    return v
        return None

    return {
        "advancers": g("上涨"),
        "decliners": g("下跌"),
        "unchanged": g("平盘", "平"),
        "limit_up": g("涨停"),
        "limit_down": g("跌停"),
    }


def sw_first_sectors() -> list[dict[str, Any]]:
    """Shenwan level-1 sector change list (name + change%).

    ``index_realtime_sw(symbol="一级行业")`` returns 指数代码/指数名称/昨收盘/
    今开盘/最新价/成交额/... — there is NO 涨跌幅 column, so derive it from
    最新价 vs 昨收盘.
    """
    df = _ak().index_realtime_sw(symbol="一级行业")
    name_c = pick_col(df, ["指数名称", "名称"])
    last_c = pick_col(df, ["最新价"])
    prev_c = pick_col(df, ["昨收盘", "昨收"])
    out: list[dict[str, Any]] = []
    if name_c is None or last_c is None or prev_c is None:
        return out
    for _, r in df.iterrows():
        last = to_float(r.get(last_c))
        prev = to_float(r.get(prev_c))
        pct = round((last - prev) / prev * 100, 2) if (last and prev) else None
        out.append({"name": str(r.get(name_c)), "change_pct": pct})
    return out


# --------------------------------------------------------------------------- #
# FX
# --------------------------------------------------------------------------- #
def pboc_midpoint(ccy: str = "美元") -> dict[str, Optional[Any]]:
    """PBOC/SAFE central parity (人民币汇率中间价) for a currency vs CNY.

    ``currency_boc_safe`` = SAFE 人民币汇率中间价; columns are 日期 + currency
    names (美元/欧元/...). The 美元 column is USD/CNY central parity.
    """
    df = _ak().currency_boc_safe()
    row = last_row(df)
    if row is None:
        return {"value": None, "date": None}
    ccy_c = pick_col(df, [ccy])
    date_c = pick_col(df, ["日期"])
    val = to_float(row.get(ccy_c)) if ccy_c else None
    # SAFE quotes the central parity per 100 foreign-currency units (USD/CNY
    # comes back as ~700, not ~7). Normalise to per-1 when the magnitude says so.
    if val is not None and val > 50:
        val = round(val / 100, 4)
    return {
        "value": val,
        "date": str(row.get(date_c)) if date_c else None,
    }


def cfets_spot(pair: str = "USD/CNY") -> dict[str, Optional[float]]:
    """CFETS interbank spot bid/ask/mid for a pair.

    ``fx_spot_quote`` returns 货币对/买报价/卖报价 for CFETS spot.
    """
    # 1) CFETS interbank spot — but it is NaN before the 09:30 CST open, which is
    #    exactly when the morning report runs. Try it, fall through if empty.
    try:
        df = _ak().fx_spot_quote()
        pair_c = pick_col(df, ["货币对", "pair"]) or df.columns[0]
        bid_c = pick_col(df, ["买", "bid"])
        ask_c = pick_col(df, ["卖", "ask"])
        row = _pair_row(df, pair_c, offshore="CNH" in pair.upper())
        if row is not None:
            bid = to_float(row.get(bid_c)) if bid_c else None
            ask = to_float(row.get(ask_c)) if ask_c else None
            if bid is not None or ask is not None:
                mid = round((bid + ask) / 2, 4) if (bid and ask) else (bid or ask)
                return {"bid": bid, "ask": ask, "mid": mid, "source": "CFETS"}
    except Exception:
        pass
    # 2) Fallback (onshore only): BOC Sina daily reference rates. 中行折算价 ≈ mid,
    #    汇买/汇卖 as bid/ask. Quoted per 100 units → normalise to per-1.
    if "CNH" not in pair.upper():
        try:
            start, end = _window(10)
            df = _ak().currency_boc_sina(symbol="美元", start_date=start, end_date=end)
            row = last_row(df)
            if row is not None:
                buy_c = pick_col(df, ["中行汇买价", "汇买"])
                sell_c = pick_col(df, ["中行钞卖价/汇卖价", "汇卖"])
                conv_c = pick_col(df, ["中行折算价", "折算"])

                def per1(c):
                    v = to_float(row.get(c)) if c else None
                    return round(v / 100, 4) if (v is not None and v > 50) else v

                bid, ask, mid = per1(buy_c), per1(sell_c), per1(conv_c)
                if mid is None and bid and ask:
                    mid = round((bid + ask) / 2, 4)
                if bid or ask or mid:
                    return {"bid": bid, "ask": ask, "mid": mid, "source": "BOC (Sina, pre-open ref)"}
        except Exception:
            pass
    return {"bid": None, "ask": None, "mid": None, "source": None}


def fx_swap_points(pair: str = "USD/CNY") -> dict[str, Optional[float]]:
    """CFETS FX swap points for 1M / 3M / 1Y on a pair.

    ``fx_swap_quote`` returns 货币对 + tenor columns (1周/1月/3月/.../1年).
    Swap points are typically quoted in pips.
    """
    df = _ak().fx_swap_quote()
    pair_c = pick_col(df, ["货币对", "pair"]) or df.columns[0]
    row = _pair_row(df, pair_c, offshore="CNH" in pair.upper())
    if row is None:
        return {"1M": None, "3M": None, "1Y": None}
    c1m = pick_col(df, ["1月", "1M", "一个月"])
    c3m = pick_col(df, ["3月", "3M", "三个月"])
    # The 1-year column header carries a space ("1 年"); match on 年.
    c1y = pick_col(df, ["1年", "1 年", "年", "1Y", "一年"])
    return {
        "1M": _parse_swap_cell(row.get(c1m)) if c1m else None,
        "3M": _parse_swap_cell(row.get(c3m)) if c3m else None,
        "1Y": _parse_swap_cell(row.get(c1y)) if c1y else None,
    }


def offshore_usdcnh() -> Optional[float]:
    """Offshore USD/CNH spot mid, best-effort from CFETS 外币对 quotes.

    ``fx_pair_quote`` carries G10 pairs and may include USD/CNH; returns the mid
    or ``None`` when the pair isn't present (free-tier gap, not fabricated).
    """
    try:
        df = _ak().fx_pair_quote()
        pair_c = pick_col(df, ["货币对", "pair"]) or df.columns[0]
        bid_c = pick_col(df, ["买", "bid"])
        ask_c = pick_col(df, ["卖", "ask"])
        row = _pair_row(df, pair_c, offshore=True)
        if row is not None:
            bid = to_float(row.get(bid_c)) if bid_c else None
            ask = to_float(row.get(ask_c)) if ask_c else None
            if bid and ask:
                return round((bid + ask) / 2, 4)
            return bid or ask
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Rates
# --------------------------------------------------------------------------- #
def repo_rates() -> dict[str, Any]:
    """Repo rates. AkShare exposes CFETS fixing repo (FR/FDR), used here as
    proxies for R007 / DR007 — the note flags the proxy口径.

    ``repo_rate_hist`` returns 日期 + FR001/FR007/FR014/FDR001/FDR007/FDR014.
    FDR007 (存款类机构定盘) ~ DR007; FR007 (银行间定盘) ~ R007.
    """
    start, end = _window()
    df = _ak().repo_rate_hist(start_date=start, end_date=end)
    row = last_row(df)
    if row is None:
        return {"dr007_proxy": None, "r007_proxy": None, "date": None, "proxy": True}
    fdr007_c = pick_col(df, ["FDR007"])
    fr007_c = pick_col(df, ["FR007"])
    date_c = pick_col(df, ["日期", "date"])
    return {
        "dr007_proxy": to_float(row.get(fdr007_c)) if fdr007_c else None,
        "r007_proxy": to_float(row.get(fr007_c)) if fr007_c else None,
        "date": str(row.get(date_c)) if date_c else None,
        "proxy": True,
    }


def shibor() -> dict[str, Optional[float]]:
    """SHIBOR overnight & 1-week latest fixings.

    ``macro_china_shibor_all`` returns a wide frame with O/N, 1W, ... rates.
    """
    df = _ak().macro_china_shibor_all()
    row = last_row(df)
    if row is None:
        return {"on": None, "1w": None}
    # Column names look like "隔夜_定价" / "1周_定价" or "O/N", "1W".
    on_c = pick_col(df, ["隔夜", "O/N", "ON"])
    w1_c = pick_col(df, ["1周", "1W", "一周"])
    return {
        "on": to_float(row.get(on_c)) if on_c else None,
        "1w": to_float(row.get(w1_c)) if w1_c else None,
    }


def cgb_yield() -> dict[str, Optional[Any]]:
    """China government bond yield: 10Y and 1Y latest.

    ``bond_china_yield`` returns the CN sovereign curve with 1年/.../10年 and a
    曲线名称 column; we keep the 国债 (treasury) curve.
    """
    start, end = _window(30)
    df = _ak().bond_china_yield(start_date=start, end_date=end)
    curve_c = pick_col(df, ["曲线名称", "name"])
    if curve_c is not None:
        gov = df[df[curve_c].astype(str).str.contains("国债")]
        if len(gov):
            df = gov
    row = last_row(df)
    if row is None:
        return {"y10": None, "y1": None, "date": None}
    y10_c = pick_col(df, ["10年"])
    y1_c = pick_col(df, ["1年"])
    date_c = pick_col(df, ["日期", "date"])
    return {
        "y10": to_float(row.get(y10_c)) if y10_c else None,
        "y1": to_float(row.get(y1_c)) if y1_c else None,
        "date": str(row.get(date_c)) if date_c else None,
    }


def lpr() -> dict[str, Optional[Any]]:
    """Loan Prime Rate: 1Y and 5Y, latest.

    ``macro_china_lpr`` returns 日期 + 1年期/5年期 LPR series.
    """
    df = _ak().macro_china_lpr()
    row = last_row(df)
    if row is None:
        return {"y1": None, "y5": None, "date": None}
    y1_c = pick_col(df, ["1年", "LPR1Y", "1Y"])
    y5_c = pick_col(df, ["5年", "LPR5Y", "5Y"])
    date_c = pick_col(df, ["日期", "TRADE_DATE", "date"])
    return {
        "y1": to_float(row.get(y1_c)) if y1_c else None,
        "y5": to_float(row.get(y5_c)) if y5_c else None,
        "date": str(row.get(date_c)) if date_c else None,
    }


# --------------------------------------------------------------------------- #
# Commodities (SHFE)
# --------------------------------------------------------------------------- #
def shfe_main(symbol: str) -> dict[str, Optional[Any]]:
    """SHFE main-continuous close + day-on-day change% for a contract.

    ``futures_main_sina`` returns 日期/开盘价/最高价/最低价/收盘价/... for the
    Sina main-continuous series; change% is computed from the prior close.
    """
    start, end = _window()
    df = _ak().futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return {"close": None, "change_pct": None, "date": None}
    close_c = pick_col(df, ["收盘价", "收盘"])
    date_c = pick_col(df, ["日期", "date"])
    close = to_float(df.iloc[-1].get(close_c)) if close_c else None
    prev = to_float(df.iloc[-2].get(close_c)) if (close_c and len(df) >= 2) else None
    chg = round((close - prev) / prev * 100, 2) if (close and prev) else None
    return {
        "close": close,
        "change_pct": chg,
        "date": str(df.iloc[-1].get(date_c)) if date_c else None,
    }


# Foreign commodity realtime symbols (Sina 外盘): OIL = ICE Brent, CL = NYMEX WTI.
# Several WTI aliases are tried in order until one returns a quote.
_FOREIGN_OIL = {"brent": ["OIL"], "wti": ["CL", "CONC", "WTI"]}


def foreign_oil() -> dict[str, dict[str, Optional[float]]]:
    """Brent & WTI realtime close + change% from AkShare (free), replacing the
    Finnhub oil symbols that the free tier doesn't cover.

    ``futures_foreign_commodity_realtime`` returns 名称/最新价/涨跌幅/... for a
    foreign contract. OIL -> 布伦特原油; CL -> NYMEX WTI.
    """
    out = {"brent": {"close": None, "change_pct": None},
           "wti": {"close": None, "change_pct": None}}
    for want, syms in _FOREIGN_OIL.items():
        for sym in syms:
            try:
                df = _ak().futures_foreign_commodity_realtime(symbol=sym)
                row = last_row(df)
                if row is None:
                    continue
                last_c = pick_col(df, ["最新价", "close"])
                pct_c = pick_col(df, ["涨跌幅", "change"])
                close = to_float(row.get(last_c)) if last_c else None
                pct = to_float(row.get(pct_c)) if pct_c else None
                if close is not None:
                    out[want]["close"] = round(close, 3)
                    out[want]["change_pct"] = round(pct, 2) if pct is not None else None
                    break  # found a working symbol for this product
            except Exception:
                continue
    return out
