"""Minimal Telegram Bot API client (just sendMessage / getUpdates via httpx)."""

from __future__ import annotations

from typing import Any, Dict, List

import httpx

API_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, bot_token: str):
        self._base = f"{API_BASE}/bot{bot_token}"
        # One long-lived client so connections are kept alive and reused.
        # Creating a client per call meant a fresh TCP + TLS handshake (and
        # re-loading the CA bundle, which blocks the event loop) for every
        # single message - slow enough on a Raspberry Pi to be noticeable.
        self._client = httpx.AsyncClient(timeout=15)

    async def send_message(self, chat_id: str, text: str) -> None:
        resp = await self._client.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        resp.raise_for_status()

    async def get_updates(self, offset: int = None, timeout: int = 0) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        resp = await self._client.get(
            f"{self._base}/getUpdates", params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def close(self) -> None:
        await self._client.aclose()
