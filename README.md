# Market-Data MCP

A Python MCP server that supplies **precise, timestamped, structured numbers**
for the daily financial morning report — replacing the parts where the report
prompt would otherwise scrape figures from web search. News and drivers stay in
the prompt's web search; **this server owns only the hard numbers.**

Deployment model is identical to the existing **gbrain / oura** MCPs: a Windows
always-on process exposed through Cloudflare Tunnel, registered as a Claude.ai
connector. The connection design and the four pitfalls it avoids come straight
from `reference/mcp-connection-playbook` in gbrain.

## Tools (one per morning-report section)

| Tool | Returns | Sources |
|---|---|---|
| `get_us_equities` | S&P 500 / Nasdaq / Dow / Russell 2000 close + intraday %, VIX, S&P sector leaders/laggards | Finnhub (ETF proxy fallback) |
| `get_us_rates` | UST 10Y / 2Y, 10s2s spread, Fed target band, DXY (best-effort) | FRED + Finnhub |
| `get_usdcny_spot` | PBOC central parity, onshore CFETS spot, offshore USDCNH, CNH-CNY basis | AkShare/SAFE/CFETS + Finnhub |
| `get_usdcny_forwards` | 1M / 3M / 1Y swap points | AkShare/CFETS |
| `get_cn_rates` | DR007 / R007, SHIBOR O/N & 1W, 10Y / 1Y CGB, 1Y / 5Y LPR | AkShare |
| `get_cn_equities` | SSE / SZSE / CSI 300 / ChiNext / STAR 50 close, %, turnover, breadth, Shenwan L1 sectors | AkShare |
| `get_commodities` | SHFE gold/silver/copper/zinc/nickel + Brent/WTI close, % | AkShare/SHFE + Finnhub |
| `get_econ_calendar` | Today/overnight releases: actual / consensus / prior | Finnhub (best-effort) |

## Output contract

Every tool returns the same envelope:

```json
{
  "as_of_local": "2026-06-24T15:00:00+0800",
  "as_of_source_tz": "2026-06-24T15:00:00 CST",
  "session_date": "2026-06-24",
  "source": "AkShare / CFETS",
  "data": { "cgb_10y": { "value": 1.78, "unit": "%" } },
  "status": "ok | partial | unavailable",
  "notes": "proxy口径 / divergence / missing-field notes"
}
```

Rules enforced in code (`mcp_market_data/contract.py`):
- A field that can't be fetched is `null` — **never a placeholder or fabricated
  number.** Consensus in particular is `null` when the feed lacks it.
- Numbers carry a unit (`bp` / `%` / `pts` / `CNY` / `USD/bbl` ...).
- Timestamps in **both** GMT+8 and the source timezone.
- Internal compute is UTC; each tool has its own try/except so one source
  outage yields `unavailable`/`partial` instead of crashing the others.

### Known gaps (handled, not hidden)
- **DXY**: Finnhub free tier has no clean symbol → `null`, marked unavailable.
- **US indices**: free-tier index coverage is thin → falls back to ETF proxies
  (SPY/QQQ/DIA/IWM), flagged in each index's `via` field and in `notes`.
- **Econ-calendar consensus**: Finnhub calendar is usually paywalled → `null`,
  never invented.
- **DR007/R007**: AkShare exposes CFETS fixing repo (FDR007/FR007) used as
  proxies; flagged in `notes` for口径 verification on the host.
- **Northbound real-time flow**: no longer published → intentionally omitted.

## Layout

```
server.py                       FastMCP entrypoint (stdio | http), OAuth wiring
mcp_market_data/
  contract.py                   envelope, q(value,unit), status derivation, tz
  cache.py                      per-session_date in-memory cache
  tools.py                      the 8 tools (assembly + error isolation)
  sources/
    akshare_source.py           China data + SHFE (no key)
    fred_source.py              FRED
    finnhub_source.py           Finnhub
tests/
  smoke_test.py                 structural/contract tests (no network)
  live_smoke.py                 RUN ON DEPLOY HOST: real-data criteria #3/#4/#5
deploy/
  cloudflared-config.example.yml
  run-market-data-mcp.cmd
  register-scheduled-task.ps1
```

## Run

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill FRED_API_KEY + FINNHUB_API_KEY

# local stdio (Claude Desktop / dev):
python server.py

# remote Streamable HTTP (behind Cloudflare Tunnel):
MCP_TRANSPORT=http MCP_AUTH=oauth PUBLIC_URL=https://market.popcult.win python server.py
```

Tests:
```bash
python -m tests.smoke_test     # no network — contract, isolation, no-fabrication, registration
python -m tests.live_smoke     # on the deploy host — real AkShare/FRED/Finnhub
```

## Deploy (gbrain/oura pattern)

1. Clone to `E:\market-data-mcp`, create venv, `pip install -r requirements.txt`.
2. `.env` with the two API keys (kept out of git).
3. Add the ingress route in `deploy/cloudflared-config.example.yml` to the
   existing tunnel; `cloudflared tunnel route dns <tunnelId> market.popcult.win`;
   restart the `cloudflared-tunnel` task.
4. `deploy/register-scheduled-task.ps1` registers the always-on task
   (`MultipleInstances=IgnoreNew`, auto-restart).
5. In Claude.ai → Connectors, add `https://market.popcult.win` (DCR + OAuth
   handled by `MCP_AUTH=oauth`). Temporarily whitelist your IP in the
   Cloudflare "Anthropic only" rule while completing the OAuth handshake if you
   gate the host like `brain.popcult.win`.
6. The morning-report prompt's data layer is already MCP-first / web-search
   fallback, so no prompt change is needed — it will call these tools.

### Connection pitfalls avoided (from the playbook)
1. **Multi-instance file-lock hang** → single Scheduled Task; in-memory cache
   means no shared auth-store file to contend on; hard-kill by port on redeploy.
2. **`trust proxy=true` rate-limit error** → FastMCP/uvicorn doesn't apply the
   express permissive-trust-proxy limiter; cloudflared stays on loopback.
3. **Confidential-client plaintext-secret mismatch** → handled inside FastMCP's
   OAuth provider; we don't roll a custom plaintext/hash comparison.
4. **Network (IPv6 dead / upstream loss / CF 530)** → layer-4, not code; if the
   connector "can't reach", compare against gbrain on the same tunnel first.

## Sandbox note

This server was built and structurally smoke-tested (116/116 checks) in a CI
sandbox whose egress proxy **blocks the data-provider hosts** (AkShare's
eastmoney/sina/CFETS endpoints, FRED, Finnhub returned 403 at the policy
proxy). The live-data acceptance criteria (#3 AkShare four-field, #4 FRED, #5
Finnhub) therefore run via `tests/live_smoke.py` **on the deployment host**,
where outbound to the providers is open.
