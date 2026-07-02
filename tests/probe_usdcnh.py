"""Find a working free source for offshore USD/CNH, and confirm the WTI symbol.

Run with the live_smoke env (proxy + NO_PROXY):
  .\.venv\Scripts\python -m tests.probe_usdcnh
"""

from __future__ import annotations

import akshare as ak
import requests


def main():
    print("========== WTI symbol candidates ==========")
    for s in ["CL", "CONC", "WTI", "USOIL"]:
        try:
            df = ak.futures_foreign_commodity_realtime(symbol=s)
            cols = [c for c in ["名称", "最新价", "涨跌幅"] if c in df.columns]
            print(f"[OK] {s}:", df[cols].to_string(index=False) if cols else df.shape)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {s}: {type(e).__name__}: {str(e)[:80]}")

    print("\n========== USD/CNH candidates ==========")
    # 1) Sina realtime forex (direct; sinajs needs a Referer)
    try:
        r = requests.get("https://hq.sinajs.cn/list=fx_susdcnh", timeout=10,
                         headers={"Referer": "https://finance.sina.com.cn"})
        print("sina fx_susdcnh raw:", repr(r.text)[:300])
    except Exception as e:  # noqa: BLE001
        print("sina fx_susdcnh FAIL:", type(e).__name__, str(e)[:80])

    # 2) Baidu forex — discover the right symbol string
    for s in ["USDCNH", "美元离岸人民币", "美元人民币", "离岸人民币"]:
        try:
            df = ak.fx_quote_baidu(symbol=s)
            print(f"[OK] fx_quote_baidu({s!r}):")
            print("   columns:", list(df.columns))
            print(df.head(4).to_string()[:400])
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] fx_quote_baidu({s!r}): {type(e).__name__}: {str(e)[:80]}")

    # 3) Foreign FX realtime symbol list (may include CNH)
    try:
        df = ak.futures_foreign_commodity_subscribe_exchange_symbol()
        print("\nsubscribe symbol list:", str(df)[:600])
    except Exception as e:  # noqa: BLE001
        print("subscribe list FAIL:", type(e).__name__, str(e)[:80])


if __name__ == "__main__":
    main()
