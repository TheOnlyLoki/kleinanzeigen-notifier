"""Manage watches from Telegram chat: /list, /add (guided), /delete.

Long-polls getUpdates (no inbound port/webhook needed - fits the Pi
deployment the same way the search watchers and Watchtower do). Only the
configured admin chat_id may issue commands, since the bot's username is
publicly discoverable on Telegram.
"""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from notifier.config import WatchConfig
from notifier.search_url import describe_search_url, extract_search_url, suggest_watch_name
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

# /addurl: every filter comes from the URL, so only name + interval are asked.
_ADD_URL_STEPS = [
    (
        "url",
        "Set up the search on kleinanzeigen.de with all the filters you want "
        "(e.g. brand, first registration, mileage, fuel, gearbox for cars), "
        "then paste its URL here.",
    ),
    ("name", "Name for this watch?"),
    ("interval_minutes", "Check interval in minutes? (send - for the default, 15)"),
]

_URL_HINT = (
    "Tip: to use all of kleinanzeigen.de's filters for a category (e.g. brand, "
    "mileage, gearbox for cars), set the search up on the site and just paste "
    "its URL here instead - or use /addurl."
)

_INT_FIELDS = {"radius", "min_price", "max_price", "interval_minutes"}

_INVALID_URL_TEXT = (
    "That doesn't look like a kleinanzeigen.de search URL. Copy the address of "
    "a search results page (starting with https://www.kleinanzeigen.de/s-...) - "
    "not a single listing. /cancel to abort."
)

HELP_TEXT = (
    "<b>Kleinanzeigen Notifier</b>\n"
    "/list - show configured watches\n"
    "/add - add a new watch (guided)\n"
    "/addurl [url] - add a watch from a kleinanzeigen.de search URL, with all its filters "
    "(or just paste the URL)\n"
    "/delete &lt;number|name&gt; - remove a watch\n"
    "/poll [number|name] - scrape now instead of waiting for the interval\n"
    "/cancel - abort an in-progress /add\n"
    "/help - this message"
)


@dataclass
class _AddWizard:
    steps: List[Tuple[str, str]] = field(default_factory=lambda: _ADD_STEPS)
    step: int = 0
    fields: Dict[str, Any] = field(default_factory=dict)


class TelegramCommandHandler:
    def __init__(self, service: NotifierService, telegram: TelegramClient, allowed_chat_id: str):
        self._service = service
        self._telegram = telegram
        self._allowed_chat_id = str(allowed_chat_id)
        self._offset: Optional[int] = None
        self._wizard: Optional[_AddWizard] = None
        self._poll_task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        # Discard any backlog (e.g. the /start used to discover chat_id) so it's not replayed.
        backlog = await self._telegram.get_updates()
        if backlog:
            self._offset = backlog[-1]["update_id"] + 1

        try:
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
        finally:
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()

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

        if not text.startswith("/"):
            # A pasted search URL starts /addurl on its own - unless a wizard
            # is past its first question and the text is an answer to it.
            url = extract_search_url(text)
            if url and (self._wizard is None or self._wizard.step == 0):
                await self._start_url_wizard(chat_id, url)
                return
            if self._wizard is not None:
                await self._advance_wizard(chat_id, text)
                return

        command = text.split(" ", 1)[0].split("@", 1)[0].lower()

        if command in ("/help", "/start"):
            await self._telegram.send_message(chat_id, HELP_TEXT)
        elif command == "/list":
            await self._cmd_list(chat_id)
        elif command == "/add":
            self._wizard = _AddWizard()
            await self._telegram.send_message(chat_id, f"{_URL_HINT}\n\n{_ADD_STEPS[0][1]}")
        elif command == "/addurl":
            arg = text.partition(" ")[2].strip()
            url = extract_search_url(arg) if arg else None
            if arg and not url:
                await self._telegram.send_message(chat_id, _INVALID_URL_TEXT)
            elif url:
                await self._start_url_wizard(chat_id, url)
            else:
                self._wizard = _AddWizard(steps=_ADD_URL_STEPS)
                await self._telegram.send_message(chat_id, _ADD_URL_STEPS[0][1])
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
            await self._start_poll(chat_id, arg)
        else:
            await self._telegram.send_message(chat_id, "Unknown command. /help for options.")

    async def _cmd_list(self, chat_id: str) -> None:
        watches = self._service.list_watches()
        if not watches:
            await self._telegram.send_message(chat_id, "No watches configured. Use /add.")
            return

        lines = []
        for i, w in enumerate(watches, start=1):
            name = html.escape(w.name)
            if w.url:
                summary = " · ".join(describe_search_url(w.url, CATEGORY_LABELS)) or "Suche"
                link = f'<a href="{html.escape(w.url, quote=True)}">\U0001F517</a>'
                lines.append(
                    f"{i}. <b>{name}</b> – {link} {html.escape(summary)} ⏱{w.interval_minutes}min"
                )
                continue

            parts = [w.query or "(any keyword)"]
            if w.category_id:
                parts.append(f"\U0001F3F7{CATEGORY_LABELS.get(w.category_id, w.category_id)}")
            if w.location:
                parts.append(f"\U0001F4CD{w.location}" + (f"+{w.radius}km" if w.radius else ""))
            if w.min_price or w.max_price:
                parts.append(f"\U0001F4B6{w.min_price or 0}-{w.max_price or '∞'}€")
            parts.append(f"⏱{w.interval_minutes}min")
            lines.append(f"{i}. <b>{name}</b> – {' '.join(parts)}")

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

    async def _start_poll(self, chat_id: str, arg: str) -> None:
        """Run /poll in the background: a scrape takes many seconds (far more
        on a Raspberry Pi), and awaiting it here would stall getUpdates so no
        other command got an answer until every watch was scraped."""
        if self._poll_task and not self._poll_task.done():
            await self._telegram.send_message(
                chat_id, "A poll is already running - results will follow."
            )
            return
        self._poll_task = asyncio.create_task(self._cmd_poll(chat_id, arg), name="telegram-poll")

    async def _cmd_poll(self, chat_id: str, arg: str) -> None:
        try:
            await self._poll(chat_id, arg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("/poll failed")

    async def _poll(self, chat_id: str, arg: str) -> None:
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
        key, _ = wizard.steps[wizard.step]
        value: Any = None if text == "-" else text

        if key == "url":
            value = extract_search_url(text)
            if not value:
                await self._telegram.send_message(chat_id, _INVALID_URL_TEXT)
                return

        if key == "name" and value is None and "url" in wizard.fields:
            value = self._suggested_name(wizard.fields["url"])

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

        if wizard.step >= len(wizard.steps):
            self._wizard = None
            watch = WatchConfig(
                name=wizard.fields["name"],
                url=wizard.fields.get("url"),
                query=wizard.fields.get("query"),
                category_id=wizard.fields.get("category_id"),
                location=wizard.fields.get("location"),
                radius=wizard.fields.get("radius"),
                min_price=wizard.fields.get("min_price"),
                max_price=wizard.fields.get("max_price"),
                interval_minutes=wizard.fields.get("interval_minutes") or 15,
            )
            await self._service.add_watch(watch)
            confirmation = f"✅ Added watch '{html.escape(watch.name)}'."
            if watch.url:
                filters = describe_search_url(watch.url, CATEGORY_LABELS)
                if filters:
                    confirmation += "\n" + html.escape(" · ".join(filters))
            await self._telegram.send_message(chat_id, confirmation)
        else:
            await self._telegram.send_message(chat_id, self._prompt(wizard))

    async def _start_url_wizard(self, chat_id: str, url: str) -> None:
        self._wizard = _AddWizard(steps=_ADD_URL_STEPS, step=1, fields={"url": url})
        filters = describe_search_url(url, CATEGORY_LABELS)
        intro = "\U0001F517 Search URL received"
        if filters:
            intro += ": " + html.escape(" · ".join(filters))
        await self._telegram.send_message(chat_id, f"{intro}\n\n{self._prompt(self._wizard)}")

    def _prompt(self, wizard: _AddWizard) -> str:
        key, prompt = wizard.steps[wizard.step]
        if key == "name" and "url" in wizard.fields:
            suggestion = self._suggested_name(wizard.fields["url"])
            return f"{prompt} (send - for '{html.escape(suggestion)}')"
        return prompt

    def _suggested_name(self, url: str) -> str:
        """Name derived from the URL, made unique among existing watches."""
        base = suggest_watch_name(url, CATEGORY_LABELS)
        taken = {w.name.lower() for w in self._service.list_watches()}
        name, n = base, 2
        while name.lower() in taken:
            name, n = f"{base} {n}", n + 1
        return name
