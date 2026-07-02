"""Persistent OAuth provider — survives server restarts.

FastMCP's ``InMemoryOAuthProvider`` keeps DCR clients + tokens in RAM, so every
restart (redeploy, reboot, crash) wipes them. Claude's connector token then
becomes invalid, and a **non-interactive routine can't re-run the OAuth browser
flow** — so the morning report silently falls back to web search instead of the
MCP. This subclass persists the store to disk (the gbrain/oura "凭证落盘"
pattern) so the routine's authorization keeps working across restarts.

Why this is enough: the access token expires after ~1h, but the refresh token
never expires (FastMCP default), and Claude's client refreshes silently via
``grant_type=refresh_token``. Persisting the client + refresh token is what lets
that refresh succeed after a restart — no human re-auth needed.

Storage is a JSON file (atomic write). It holds bearer tokens for a read-only
market-data server, kept out of git via .gitignore; encrypt at rest if you want
to match gbrain's AES-on-disk exactly.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastmcp.server.auth.providers.in_memory import (
    AccessToken,
    InMemoryOAuthProvider,
    OAuthClientInformationFull,
    RefreshToken,
)


class PersistentOAuthProvider(InMemoryOAuthProvider):
    """InMemoryOAuthProvider that loads on init and saves after every mutation."""

    def __init__(self, *args, store_path: str | os.PathLike, **kwargs):
        super().__init__(*args, **kwargs)
        self._store_path = Path(store_path)
        self._save_lock = threading.Lock()
        self._load()

    # --- persistence -------------------------------------------------------- #
    def _load(self) -> None:
        try:
            raw = json.loads(self._store_path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            # Corrupt store: start fresh rather than refuse to boot.
            return
        try:
            self.clients = {
                k: OAuthClientInformationFull.model_validate(v)
                for k, v in raw.get("clients", {}).items()
            }
            self.access_tokens = {
                k: AccessToken.model_validate(v)
                for k, v in raw.get("access_tokens", {}).items()
            }
            self.refresh_tokens = {
                k: RefreshToken.model_validate(v)
                for k, v in raw.get("refresh_tokens", {}).items()
            }
            self._access_to_refresh_map = dict(raw.get("a2r", {}))
            self._refresh_to_access_map = dict(raw.get("r2a", {}))
        except Exception:
            # Partial/incompatible data — reset to empty, don't crash startup.
            self.clients, self.access_tokens, self.refresh_tokens = {}, {}, {}
            self._access_to_refresh_map, self._refresh_to_access_map = {}, {}

    def _save(self) -> None:
        data = {
            "clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
            "access_tokens": {k: v.model_dump(mode="json") for k, v in self.access_tokens.items()},
            "refresh_tokens": {k: v.model_dump(mode="json") for k, v in self.refresh_tokens.items()},
            "a2r": self._access_to_refresh_map,
            "r2a": self._refresh_to_access_map,
        }
        with self._save_lock:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            os.replace(tmp, self._store_path)  # atomic swap

    # --- mutating methods: persist after each ------------------------------- #
    async def register_client(self, client_info) -> None:
        await super().register_client(client_info)
        self._save()

    async def exchange_authorization_code(self, client, authorization_code):
        result = await super().exchange_authorization_code(client, authorization_code)
        self._save()
        return result

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        result = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save()
        return result

    async def revoke_token(self, token):
        result = await super().revoke_token(token)
        self._save()
        return result
