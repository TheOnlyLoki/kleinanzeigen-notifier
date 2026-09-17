"""Minimal Telegram Bot API client (just sendMessage / getUpdates via httpx)."""

from __future__ import annotations

from typing import Any, Dict, List

import httpx

API_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, bot_token: str):
        self._base = f"{API_BASE}/bot{bot_token}"

    async def send_message(self, chat_id: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
            )
            resp.raise_for_status()

    async def get_updates(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self._base}/getUpdates")
            resp.raise_for_status()
            return resp.json().get("result", [])
