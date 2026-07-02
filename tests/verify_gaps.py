"""Verify the gap fixes with live data on the deploy host.

Calls the four affected tools and prints exactly the fields that were empty
before, so we can confirm each fix produced a real value.

Run with the live_smoke env (proxy + NO_PROXY + .env loaded):
  .\.venv\Scripts\python -m tests.verify_gaps
"""

from __future__ import annotations

from mcp_market_data import tools


def leaf(d, *path):
    for p in path:
        d = d[p]
    return d.get("value") if isinstance(d, dict) and "value" in d else d


def main():
    spot = tools.get_usdcny_spot()
    print("\n=== get_usdcny_spot ===  status:", spot["status"])
    print("  onshore mid/bid/ask:",
          leaf(spot, "data", "onshore_spot", "mid"),
          leaf(spot, "data", "onshore_spot", "bid"),
          leaf(spot, "data", "onshore_spot", "ask"))
    print("  offshore USDCNH:", leaf(spot, "data", "offshore_usdcnh"))
    print("  notes:", spot["notes"])

    fwd = tools.get_usdcny_forwards()
    print("\n=== get_usdcny_forwards ===  status:", fwd["status"])
    print("  swap 1M/3M/1Y:",
          leaf(fwd, "data", "swap_1m"), leaf(fwd, "data", "swap_3m"), leaf(fwd, "data", "swap_1y"))
    print("  notes:", fwd["notes"])

    cn = tools.get_cn_equities()
    print("\n=== get_cn_equities ===  status:", cn["status"])
    lead = cn["data"].get("sw_l1_leaders")
    lag = cn["data"].get("sw_l1_laggards")
    print("  SW L1 leaders:", [(s["name"], s["change_pct"]["value"]) for s in lead] if lead else None)
    print("  SW L1 laggards:", [(s["name"], s["change_pct"]["value"]) for s in lag] if lag else None)
    print("  notes:", cn["notes"])

    com = tools.get_commodities()
    print("\n=== get_commodities ===  status:", com["status"])
    print("  Brent close/%:", leaf(com, "data", "brent", "close"), leaf(com, "data", "brent", "change_pct"))
    print("  WTI   close/%:", leaf(com, "data", "wti", "close"), leaf(com, "data", "wti", "change_pct"))
    print("  notes:", com["notes"])


if __name__ == "__main__":
    main()
