"""Manage watches from Telegram chat: /list, /add (guided), /delete.

Long-polls getUpdates (no inbound port/webhook needed - fits the Pi
deployment the same way the search watchers and Watchtower do). Only the
configured admin chat_id may issue commands, since the bot's username is
publicly discoverable on Telegram.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

from notifier.config import WatchConfig
from notifier.service import NotifierService
from notifier.telegram import TelegramClient
from notifier.watchers import format_event

ALLOWED_RADII = (5, 10, 20, 30, 50, 100, 150, 200)

# A few common kleinanzeigen.de vehicle categories (id visible in its own URLs,
# e.g. kleinanzeigen.de/s-autos/c216). Not exhaustive - a raw numeric id is
# also accepted, so any other category is still reachable by looking up its
# id on the site.
CATEGORY_LABELS = {
    216: "Auto",
    223: "Autoteile",
    305: "Motorrad",
    217: "Fahrrad",
    220: "Wohnwagen",
    211: "Boot",
}
CATEGORIES = {
    "auto": 216,
    "autos": 216,
    "autoteile": 223,
    "motorrad": 305,
    "motorräder": 305,
    "motorraeder": 305,
    "fahrrad": 217,
    "fahrräder": 217,
    "fahrraeder": 217,
    "wohnwagen": 220,
    "wohnmobil": 220,
    "boot": 211,
    "boote": 211,
}

_ADD_STEPS = [
    ("name", "Name for this watch?"),
    ("query", "Search keywords? (send - for none)"),
    (
        "category_id",
        "Category? (e.g. "
        + ", ".join(sorted(set(CATEGORY_LABELS.values()), key=str.lower))
        + " - or a numeric kleinanzeigen.de category id. Send - for none/all)",
    ),
    ("location", "Location - PLZ or place name? (send - for none)"),
    ("radius", f"Radius in km ({', '.join(map(str, ALLOWED_RADII))})? (send - for none)"),
    ("min_price", "Minimum price in €? (send - for none)"),
    ("max_price", "Maximum price in €? (send - for none)"),
    ("interval_minutes", "Check interval in minutes? (send - for the default, 15)"),
]

_INT_FIELDS = {"radius", "min_price", "max_price", "interval_minutes"}

HELP_TEXT = (
    "<b>Kleinanzeigen Notifier</b>\n"
    "/list - show configured watches\n"
    "/add - add a new watch (guided)\n"
    "/delete &lt;number|name&gt; - remove a watch\n"
    "/poll [number|name] - scrape now instead of waiting for the interval\n"
    "/cancel - abort an in-progress /add\n"
    "/help - this message"
)


@dataclass
class _AddWizard:
    step: int = 0
    fields: Dict[str, Any] = field(default_factory=dict)


class TelegramCommandHandler:
    def __init__(self, service: NotifierService, telegram: TelegramClient, allowed_chat_id: str):
        self._service = service
        self._telegram = telegram
        self._allowed_chat_id = str(allowed_chat_id)
        self._offset: Optional[int] = None
        self._wizard: Optional[_AddWizard] = None

    async def run(self) -> None:
        # Discard any backlog (e.g. the /start used to discover chat_id) so it's not replayed.
        backlog = await self._telegram.get_updates()
        if backlog:
            self._offset = backlog[-1]["update_id"] + 1

        while True:
            try:
                updates = await self._telegram.get_updates(offset=self._offset, timeout=30)
                for update in updates:
                    self._offset = update["update_id"] + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram command poll failed")
                await asyncio.sleep(5)

    async def _handle_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()

        if not text:
            return
        if chat_id != self._allowed_chat_id:
            logger.warning(f"Ignoring command from unauthorized chat {chat_id}: {text!r}")
            return

        if self._wizard is not None and not text.startswith("/"):
            await self._advance_wizard(chat_id, text)
            return

        command = text.split(" ", 1)[0].split("@", 1)[0].lower()

        if command in ("/help", "/start"):
            await self._telegram.send_message(chat_id, HELP_TEXT)
        elif command == "/list":
            await self._cmd_list(chat_id)
        elif command == "/add":
            self._wizard = _AddWizard()
            await self._telegram.send_message(chat_id, _ADD_STEPS[0][1])
        elif command == "/cancel":
            had_wizard = self._wizard is not None
            self._wizard = None
            await self._telegram.send_message(
                chat_id, "Cancelled." if had_wizard else "Nothing to cancel."
            )
        elif command == "/delete":
            arg = text.partition(" ")[2].strip()
            await self._cmd_delete(chat_id, arg)
        elif command == "/poll":
            arg = text.partition(" ")[2].strip()
            await self._cmd_poll(chat_id, arg)
        else:
            await self._telegram.send_message(chat_id, "Unknown command. /help for options.")

    async def _cmd_list(self, chat_id: str) -> None:
        watches = self._service.list_watches()
        if not watches:
            await self._telegram.send_message(chat_id, "No watches configured. Use /add.")
            return

        lines = []
        for i, w in enumerate(watches, start=1):
            parts = [w.query or "(any keyword)"]
            if w.category_id:
                parts.append(f"\U0001F3F7{CATEGORY_LABELS.get(w.category_id, w.category_id)}")
            if w.location:
                parts.append(f"\U0001F4CD{w.location}" + (f"+{w.radius}km" if w.radius else ""))
            if w.min_price or w.max_price:
                parts.append(f"\U0001F4B6{w.min_price or 0}-{w.max_price or '∞'}€")
            parts.append(f"⏱{w.interval_minutes}min")
            lines.append(f"{i}. <b>{w.name}</b> – {' '.join(parts)}")

        await self._telegram.send_message(chat_id, "\n".join(lines))

    def _resolve_watch_name(self, arg: str) -> Optional[str]:
        watches = self._service.list_watches()
        if arg.isdigit():
            idx = int(arg) - 1
            return watches[idx].name if 0 <= idx < len(watches) else None
        for w in watches:
            if w.name.lower() == arg.lower():
                return w.name
        return None

    async def _cmd_delete(self, chat_id: str, arg: str) -> None:
        target = self._resolve_watch_name(arg) if arg else None
        if not target:
            await self._telegram.send_message(
                chat_id, "Usage: /delete <number from /list, or exact name>"
            )
            return

        removed = await self._service.remove_watch(target)
        await self._telegram.send_message(
            chat_id, f"\U0001F5D1 Removed '{target}'." if removed else f"Couldn't find '{target}'."
        )

    async def _cmd_poll(self, chat_id: str, arg: str) -> None:
        watches = self._service.list_watches()
        if not watches:
            await self._telegram.send_message(chat_id, "No watches configured. Use /add.")
            return

        if arg:
            target = self._resolve_watch_name(arg)
            if not target:
                await self._telegram.send_message(
                    chat_id, "Usage: /poll [number from /list, or exact name]"
                )
                return
            targets = [target]
        else:
            targets = [w.name for w in watches]

        await self._telegram.send_message(
            chat_id, f"\U0001F504 Polling {len(targets)} watch(es) now..."
        )

        for name in targets:
            try:
                events = await self._service.poll_watch(name)
            except Exception:
                logger.exception(f"[{name}] manual poll failed")
                await self._telegram.send_message(chat_id, f"⚠️ Poll failed for '{name}'.")
                continue

            if events:
                for event in events:
                    await self._telegram.send_message(chat_id, format_event(event))
            else:
                await self._telegram.send_message(chat_id, f"'{name}': no new results.")

    async def _advance_wizard(self, chat_id: str, text: str) -> None:
        wizard = self._wizard
        assert wizard is not None
        key, _ = _ADD_STEPS[wizard.step]
        value: Any = None if text == "-" else text

        if key == "category_id" and value is not None:
            if value.isdigit():
                value = int(value)
            elif value.lower() in CATEGORIES:
                value = CATEGORIES[value.lower()]
            else:
                known = ", ".join(sorted(set(CATEGORY_LABELS.values()), key=str.lower))
                await self._telegram.send_message(
                    chat_id, f"Unknown category. Try one of: {known} - or a numeric category id."
                )
                return

        if key in _INT_FIELDS and value is not None:
            try:
                value = int(value)
            except ValueError:
                await self._telegram.send_message(chat_id, "Please send a whole number, or - to skip.")
                return
            if key == "radius" and value not in ALLOWED_RADII:
                await self._telegram.send_message(
                    chat_id, f"Radius must be one of: {', '.join(map(str, ALLOWED_RADII))}"
                )
                return

        if key == "name":
            if not value:
                await self._telegram.send_message(chat_id, "Name can't be empty.")
                return
            if any(w.name.lower() == value.lower() for w in self._service.list_watches()):
                await self._telegram.send_message(chat_id, "A watch with that name already exists.")
                return

        wizard.fields[key] = value
        wizard.step += 1

        if wizard.step >= len(_ADD_STEPS):
            self._wizard = None
            watch = WatchConfig(
                name=wizard.fields["name"],
                query=wizard.fields.get("query"),
                category_id=wizard.fields.get("category_id"),
                location=wizard.fields.get("location"),
                radius=wizard.fields.get("radius"),
                min_price=wizard.fields.get("min_price"),
                max_price=wizard.fields.get("max_price"),
                interval_minutes=wizard.fields.get("interval_minutes") or 15,
            )
            await self._service.add_watch(watch)
            await self._telegram.send_message(chat_id, f"✅ Added watch '{watch.name}'.")
        else:
            await self._telegram.send_message(chat_id, _ADD_STEPS[wizard.step][1])
