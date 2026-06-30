"""Diagnostic probe for the CN-index fetch failure on the deploy host.

get_cn_rates works (chinamoney + datacenter-web.eastmoney) but the indices
(push2his.eastmoney) come back empty. This pinpoints WHICH eastmoney host
works via WHICH path (direct vs proxy) and which akshare index function
succeeds, so we can fix the NO_PROXY split and/or swap the index endpoint.

Run with the SAME env as live_smoke (HTTPS_PROXY + NO_PROXY set):
  .\.venv\Scripts\python -m tests.probe_net
"""

from __future__ import annotations

import os

import requests

PROXY = os.environ.get("HTTPS_PROXY")


def raw(url: str, via_proxy: bool) -> str:
    proxies = {"http": PROXY, "https": PROXY} if via_proxy else {"http": None, "https": None}
    try:
        r = requests.get(
            url, timeout=15, proxies=proxies, headers={"User-Agent": "Mozilla/5.0"}
        )
        return f"{r.status_code} len={len(r.content)}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e)[:90]}"


def main() -> None:
    print("HTTPS_PROXY =", PROXY)
    print("NO_PROXY    =", os.environ.get("NO_PROXY"))
    print()

    hosts = {
        "push2his.eastmoney (index kline, FAILING)":
            "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000300"
            "&fields1=f1&fields2=f51,f53&klt=101&fqt=0&beg=20260601&end=20260630",
        "push2.eastmoney (realtime spot)":
            "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&fs=b:MK0010&fields=f12,f14,f2",
        "datacenter-web.eastmoney (WORKS)":
            "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_RATE&columns=ALL&pageSize=1",
    }
    for label, url in hosts.items():
        print(label)
        print(f"   direct : {raw(url, False)}")
        print(f"   proxy  : {raw(url, True)}")
        print()

    print("--- akshare index functions ---")
    import akshare as ak

    probes = [
        ("index_zh_a_hist 000300",
         lambda: ak.index_zh_a_hist(symbol="000300", period="daily",
                                    start_date="20260601", end_date="20260630")),
        ("stock_zh_index_daily_em sh000300",
         lambda: ak.stock_zh_index_daily_em(symbol="sh000300")),
        ("stock_zh_index_spot_em 沪深重要指数",
         lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数")),
        ("stock_zh_index_spot_sina",
         lambda: ak.stock_zh_index_spot_sina()),
    ]
    for name, fn in probes:
        try:
            df = fn()
            print(f"[OK]   {name}  shape={getattr(df, 'shape', None)}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}  {type(e).__name__}: {str(e)[:110]}")


if __name__ == "__main__":
    main()
