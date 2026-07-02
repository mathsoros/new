"""Diagnose the morning-report data gaps on the deploy host.

Dumps the REAL shape/columns/sample of the AkShare functions that returned
empty (CFETS spot, CFETS swap, Shenwan L1), and probes candidate free
alternatives for the Finnhub free-tier gaps (offshore USDCNH, Brent/WTI).

Run with the same env as live_smoke (proxy + NO_PROXY):
  .\.venv\Scripts\python -m tests.probe_gaps
"""

from __future__ import annotations


def dump(name, fn, show_cols=True, sample=6):
    try:
        df = fn()
        shape = getattr(df, "shape", None)
        print(f"\n[OK] {name}  shape={shape}")
        if hasattr(df, "columns"):
            print("   columns:", list(df.columns))
            print(df.head(sample).to_string()[:1200])
        else:
            print("   value:", str(df)[:400])
    except Exception as e:  # noqa: BLE001
        print(f"\n[FAIL] {name}  {type(e).__name__}: {str(e)[:160]}")


def main():
    import akshare as ak

    print("==================== A. failing CFETS / Shenwan ====================")
    dump("fx_spot_quote()", lambda: ak.fx_spot_quote())
    dump("fx_swap_quote()", lambda: ak.fx_swap_quote())
    for sym in ["一级行业", "二级行业", "市场表征"]:
        dump(f"index_realtime_sw(symbol={sym!r})", lambda s=sym: ak.index_realtime_sw(symbol=s))

    print("\n==================== B. offshore USDCNH candidates ====================")
    dump("fx_quote_baidu(symbol='美元人民币')", lambda: ak.fx_quote_baidu(symbol="美元人民币"))
    dump("fx_pair_quote()", lambda: ak.fx_pair_quote())
    dump("currency_boc_sina(symbol='美元', start,end)",
         lambda: ak.currency_boc_sina(symbol="美元", start_date="20260625", end_date="20260701"))

    print("\n==================== B. Brent / WTI candidates ====================")
    dump("energy_oil_hist()", lambda: ak.energy_oil_hist())
    dump("futures_global_spot_em()", lambda: ak.futures_global_spot_em())
    dump("index_global_spot_em()", lambda: ak.index_global_spot_em())
    dump("futures_foreign_commodity_realtime(['OIL'])",
         lambda: ak.futures_foreign_commodity_realtime(symbol="OIL"))


if __name__ == "__main__":
    main()
