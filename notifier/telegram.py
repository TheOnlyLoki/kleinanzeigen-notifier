"""Minimal Telegram Bot API client (messages, inline keyboards, getUpdates via httpx)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

API_BASE = "https://api.telegram.org"

# [[(button text, callback_data), ...], ...] - one inner list per row
Keyboard = List[List[tuple]]


def _markup(keyboard: Optional[Keyboard]) -> Optional[Dict[str, Any]]:
    if keyboard is None:
        return None
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in keyboard
        ]
    }


class TelegramClient:
    def __init__(self, bot_token: str):
        self._base = f"{API_BASE}/bot{bot_token}"
        # One long-lived client so connections are kept alive and reused.
        # Creating a client per call meant a fresh TCP + TLS handshake (and
        # re-loading the CA bundle, which blocks the event loop) for every
        # single message - slow enough on a Raspberry Pi to be noticeable.
        self._client = httpx.AsyncClient(timeout=15)

    async def _call(self, method: str, payload: Dict[str, Any]) -> Any:
        resp = await self._client.post(f"{self._base}/{method}", json=payload)
        resp.raise_for_status()
        return resp.json().get("result")

    async def send_message(
        self, chat_id: str, text: str, keyboard: Optional[Keyboard] = None
    ) -> Optional[int]:
        """Returns the new message's id (needed to edit it later)."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if keyboard is not None:
            payload["reply_markup"] = _markup(keyboard)
        result = await self._call("sendMessage", payload)
        return (result or {}).get("message_id")

    async def edit_message(
        self, chat_id: str, message_id: int, text: str, keyboard: Optional[Keyboard] = None
    ) -> None:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": _markup(keyboard) or {"inline_keyboard": []},
        }
        try:
            await self._call("editMessageText", payload)
        except httpx.HTTPStatusError as e:
            # Re-rendering an unchanged view is harmless, not an error.
            if "message is not modified" not in e.response.text:
                raise

    async def answer_callback(self, callback_query_id: str, text: Optional[str] = None) -> None:
        """Acknowledge a button press - until then Telegram shows a spinner on it."""
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)

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
