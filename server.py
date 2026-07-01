"""Market-Data MCP server.

Runs the 8 morning-report tools over either:
  * ``stdio``  — for local dev / Claude Desktop config, and CI smoke tests.
  * ``http``   — Streamable HTTP, for the Windows always-on + Cloudflare Tunnel
                 remote-connector deployment (same model as gbrain / oura).

Remote-connector auth (the gbrain/oura playbook, section 0):
  Claude's remote connectors require the MCP server to BE an OAuth 2.1
  authorization server + protected resource (DCR + authorization_code + PKCE).
  FastMCP 3.x ships exactly this as ``InMemoryOAuthProvider`` — the Python
  analogue of gbrain's ``mcpAuthRouter`` + ``OAuthServerProvider``. Enable it
  with ``MCP_AUTH=oauth`` and set ``PUBLIC_URL`` to the public https origin
  (e.g. https://market.popcult.win) so the OAuth issuer matches the public
  domain — mismatched issuer is a documented rejection cause.

Playbook pitfalls applied here (see reference/mcp-connection-playbook):
  #1 single instance: run under ONE Scheduled Task with MultipleInstances=
     IgnoreNew, and hard-kill by port before redeploy. The cache is in-memory
     (no shared state file) so there is no auth-store lock to contend on.
  #2 trust-proxy/rate-limit: FastMCP/uvicorn does not apply express-style
     permissive-trust-proxy rate limiting, so the express ERR_ERL pitfall does
     not arise; keep cloudflared on loopback regardless.
  #3 confidential-client secret: handled inside FastMCP's OAuth provider
     (DCR-issued clients are matched consistently); we do not roll our own
     plaintext/hash comparison.
  #4 network: a layer-4 concern (IPv6 / upstream loss / CF 530), not code.
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from mcp_market_data.tools import ALL_TOOLS

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("market_data_mcp.server")


def _build_auth():
    """Return a FastMCP AuthProvider when MCP_AUTH=oauth, else None.

    PUBLIC_URL MUST equal the public https origin or strict OAuth clients reject
    the issuer (playbook section 1). In-memory means DCR clients reset on
    restart — for production persistence, swap to a KV-backed provider; this is
    fine for an always-on single process that rarely restarts.
    """
    if os.environ.get("MCP_AUTH", "").lower() != "oauth":
        return None
    public_url = os.environ.get("PUBLIC_URL")
    if not public_url:
        raise SystemExit("MCP_AUTH=oauth requires PUBLIC_URL=https://<your-domain>")
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

    log.info("OAuth enabled; issuer=%s", public_url)
    return InMemoryOAuthProvider(base_url=public_url)


def build_server() -> FastMCP:
    mcp = FastMCP(
        name="market-data-mcp",
        version="0.1.0",
        instructions=(
            "Precise, timestamped, structured market numbers for the daily "
            "financial morning report. Each tool returns value+unit+source+"
            "as-of (GMT+8 and source tz). Missing data is null with status "
            "partial/unavailable — never fabricated. News/drivers stay in web "
            "search; this server owns only hard numbers."
        ),
        auth=_build_auth(),
    )
    # Register each tool; FastMCP uses the function name + docstring as the
    # tool's name + description, so the morning-report prompt sees rich help.
    for fn in ALL_TOOLS.values():
        mcp.tool(fn)
    return mcp


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    mcp = build_server()
    if transport in ("http", "streamable-http"):
        host = os.environ.get("HOST", "127.0.0.1")  # cloudflared lives on loopback
        port = int(os.environ.get("PORT", "8790"))
        log.info("Starting Streamable HTTP on %s:%s", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        log.info("Starting stdio transport")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
