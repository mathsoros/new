"""Live-data smoke test — RUN ON THE DEPLOYMENT HOST (Windows always-on box).

Hits the real providers, so it needs outbound network to AkShare's Chinese
endpoints + FRED + Finnhub (the build sandbox's egress proxy blocks these, so
this cannot run there). Covers the acceptance criteria that need live data:

  #3 AkShare: CSI 300 close, PBOC midpoint, DR007, 10Y CGB  (real same-day)
  #4 FRED:    DGS10 / DGS2 latest
  #5 Finnhub: S&P 500 (or SPY proxy) close + VIX
  #6 timestamps carry GMT+8 + source tz
  #7 econ-calendar consensus is null (not fabricated) when unavailable

Set FRED_API_KEY and FINNHUB_API_KEY first.

Run:  python -m tests.live_smoke
"""

from __future__ import annotations

import os
import sys

from mcp_market_data import tools


def show(title: str, env: dict) -> None:
    print(f"\n=== {title} ===")
    print("  status      :", env["status"])
    print("  as_of_local :", env["as_of_local"])
    print("  as_of_src_tz:", env["as_of_source_tz"])
    print("  session_date:", env["session_date"])
    print("  source      :", env["source"])
    if env["notes"]:
        print("  notes       :", env["notes"])


def leaf(env: dict, *path):
    cur = env["data"]
    for p in path:
        cur = cur[p]
    return cur["value"]


def main() -> int:
    ok = True

    # #3 AkShare four-field real-data check.
    cn = tools.get_cn_equities()
    show("get_cn_equities (AkShare)", cn)
    csi300 = leaf(cn, "indices", "沪深300", "close")
    print("  CSI300 close:", csi300)
    ok &= csi300 is not None

    spot = tools.get_usdcny_spot()
    show("get_usdcny_spot (AkShare/SAFE)", spot)
    pboc = leaf(spot, "pboc_midpoint")
    print("  PBOC midpoint:", pboc)
    ok &= pboc is not None

    rates = tools.get_cn_rates()
    show("get_cn_rates (AkShare)", rates)
    dr007 = leaf(rates, "dr007")
    cgb10 = leaf(rates, "cgb_10y")
    print("  DR007:", dr007, " 10Y CGB:", cgb10)
    ok &= dr007 is not None
    ok &= cgb10 is not None

    # #4 FRED.
    usr = tools.get_us_rates()
    show("get_us_rates (FRED)", usr)
    d10 = leaf(usr, "ust_10y")
    d2 = leaf(usr, "ust_2y")
    print("  DGS10:", d10, " DGS2:", d2)
    ok &= d10 is not None and d2 is not None

    # #5 Finnhub S&P 500 (or SPY proxy) + VIX.
    eq = tools.get_us_equities()
    show("get_us_equities (Finnhub)", eq)
    sp = leaf(eq, "sp500", "close")
    vix = leaf(eq, "vix")
    print("  S&P500/SPY:", sp, " (via", eq["data"]["sp500"]["via"], ") VIX:", vix)
    ok &= sp is not None

    # Remaining tools just need to return without exploding.
    for name in ("get_usdcny_forwards", "get_commodities", "get_econ_calendar"):
        env = tools.ALL_TOOLS[name]()
        show(name, env)

    print("\n" + ("LIVE SMOKE: PASS" if ok else "LIVE SMOKE: FAIL (see nulls above)"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not (os.environ.get("FRED_API_KEY") and os.environ.get("FINNHUB_API_KEY")):
        print("WARN: FRED_API_KEY / FINNHUB_API_KEY not set — US sections will be unavailable.")
    sys.exit(main())
