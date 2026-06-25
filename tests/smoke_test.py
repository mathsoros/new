"""Structural smoke tests (no network).

The build sandbox's egress proxy blocks AkShare/FRED/Finnhub hosts, so these
tests mock the source adapters and verify the parts that do not need live data:
  - the output contract is well-formed (criteria #1, #6),
  - a dead source degrades to ``unavailable`` without aborting (criterion #2),
  - consensus is never fabricated (criterion #7),
  - all 8 tools register on the FastMCP server (criterion #1).

Live-data smoke tests (criteria #3/#4/#5) are in ``live_smoke.py`` and must run
on the deployment host, where outbound to the data providers is open.

Run:  python -m tests.smoke_test
"""

from __future__ import annotations

import re
import sys

from mcp_market_data import cache
from mcp_market_data import contract as C
from mcp_market_data import tools
from mcp_market_data.sources import akshare_source as ak_src
from mcp_market_data.sources import finnhub_source as fh
from mcp_market_data.sources import fred_source as fred

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((PASS if cond else FAIL, name, detail))


ISO_LOCAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$")
ISO_SRC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} [A-Za-z0-9+-]+$")


def assert_envelope(name: str, env: dict) -> None:
    for k in ("as_of_local", "as_of_source_tz", "session_date", "source", "data", "status", "notes"):
        check(f"{name}: has '{k}'", k in env, str(env.keys()))
    check(f"{name}: status valid", env.get("status") in (C.OK, C.PARTIAL, C.UNAVAILABLE), env.get("status"))
    check(f"{name}: as_of_local GMT+8 (+0800)", bool(ISO_LOCAL.match(env.get("as_of_local", ""))) and env["as_of_local"].endswith("+0800"), env.get("as_of_local"))
    check(f"{name}: as_of_source_tz labelled", bool(ISO_SRC.match(env.get("as_of_source_tz", ""))), env.get("as_of_source_tz"))


# --------------------------------------------------------------------------- #
# Mocks: deterministic source layer, no network.
# --------------------------------------------------------------------------- #
def install_happy_mocks() -> None:
    cache.clear()
    ak_src.index_daily = lambda code: {"close": 3900.12, "change_pct": 0.85, "amount_yi": 4521.3, "date": "2026-06-24"}
    ak_src.market_breadth = lambda: {"advancers": 3200, "decliners": 1800, "unchanged": 100, "limit_up": 40, "limit_down": 5}
    ak_src.sw_first_sectors = lambda: [{"name": "电子", "change_pct": 2.1}, {"name": "银行", "change_pct": -0.4}, {"name": "煤炭", "change_pct": 1.0}]
    ak_src.pboc_midpoint = lambda ccy="美元": {"value": 7.1234, "date": "2026-06-24"}
    ak_src.cfets_spot = lambda pair="USD/CNY": {"bid": 7.18, "ask": 7.19, "mid": 7.185}
    ak_src.fx_swap_points = lambda pair="USD/CNY": {"1M": -120.0, "3M": -340.0, "1Y": -1300.0}
    ak_src.repo_rates = lambda: {"dr007_proxy": 1.85, "r007_proxy": 1.95, "date": "2026-06-24", "proxy": True}
    ak_src.shibor = lambda: {"on": 1.6, "1w": 1.8}
    ak_src.cgb_yield = lambda: {"y10": 1.78, "y1": 1.45, "date": "2026-06-24"}
    ak_src.lpr = lambda: {"y1": 3.0, "y5": 3.5, "date": "2026-06-24"}
    ak_src.shfe_main = lambda sym: {"close": 560.0, "change_pct": 0.3, "date": "2026-06-24"}
    fh.safe_quote = lambda symbol: {"current": 100.0, "change": 1.0, "change_pct": 1.0, "prev_close": 99.0, "epoch": 1750000000}
    fred.fetch_many = lambda ids: {k: (4.25 if "10" in v else 4.75, "2026-06-24") for k, v in ids.items()}
    fh.economic_calendar = lambda: None  # default: calendar paywalled


def main() -> int:
    # 1. Envelope builder + tz formatting.
    env = C.envelope(source="X", data={"a": C.q(1.0, "%")}, source_tz="America/New_York")
    assert_envelope("envelope()", env)
    check("derive_status ok", C.derive_status({"a": C.q(1, "%"), "b": C.q(2, "%")}) == C.OK)
    check("derive_status partial", C.derive_status({"a": C.q(1, "%"), "b": C.q(None, "%")}) == C.PARTIAL)
    check("derive_status unavailable", C.derive_status({"a": C.q(None, "%")}) == C.UNAVAILABLE)

    # 2. Happy path: every tool returns a valid envelope with status ok/partial.
    install_happy_mocks()
    for name, fn in tools.ALL_TOOLS.items():
        env = fn()
        assert_envelope(name, env)

    # 3. Each tool returns ok or partial when sources are healthy (not unavailable).
    install_happy_mocks()
    for name, fn in tools.ALL_TOOLS.items():
        if name == "get_econ_calendar":
            continue  # calendar is paywalled in the happy mock -> unavailable is correct
        env = fn()
        check(f"{name}: healthy => not unavailable", env["status"] != C.UNAVAILABLE, env["status"] + " / " + env["notes"])

    # 4. Error isolation: a thrown source degrades that section, not the process.
    install_happy_mocks()
    def boom(*a, **k):
        raise RuntimeError("simulated outage")
    ak_src.cgb_yield = boom  # break one CN-rates source
    env = tools.get_cn_rates()
    check("isolation: get_cn_rates survives a dead source", env["status"] in (C.PARTIAL, C.UNAVAILABLE), env["status"])
    check("isolation: cgb_10y is null after outage", env["data"]["cgb_10y"]["value"] is None)
    check("isolation: lpr still populated", env["data"]["lpr_1y"]["value"] == 3.0)

    # 5. No fabricated consensus: empty calendar => unavailable, no invented numbers.
    install_happy_mocks()
    fh.economic_calendar = lambda: None
    env = tools.get_econ_calendar()
    check("econ calendar: unavailable when paywalled", env["status"] == C.UNAVAILABLE, env["status"])
    check("econ calendar: no fabricated values", env["data"].get("events", []) == [])

    # 5b. Calendar with a row missing consensus => consensus stays null.
    install_happy_mocks()
    from datetime import datetime
    today = datetime.utcnow().date().isoformat()
    fh.economic_calendar = lambda: [
        {"event": "CPI", "country": "US", "time": today, "actual": 3.2, "estimate": None, "prev": 3.1, "unit": "%"}
    ]
    env = tools.get_econ_calendar()
    ev0 = env["data"]["events"][0]
    check("econ calendar: missing consensus => null", ev0["consensus"]["value"] is None, str(ev0))
    check("econ calendar: actual passed through", ev0["actual"]["value"] == 3.2)

    # 6. All 8 tools register on the FastMCP server.
    import importlib
    server = importlib.import_module("server")
    mcp = server.build_server()
    import asyncio
    tool_list = asyncio.run(mcp.list_tools())
    registered = {t.name for t in tool_list}
    check("server: 8 tools registered", len(registered) == 8, f"got {len(registered)}: {sorted(registered)}")
    for name in tools.ALL_TOOLS:
        check(f"server: '{name}' registered", name in registered)

    # Report
    fails = [r for r in _results if r[0] == FAIL]
    for status, name, detail in _results:
        if status == FAIL:
            print(f"[FAIL] {name}  -- {detail}")
    print(f"\n{len(_results) - len(fails)}/{len(_results)} checks passed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
