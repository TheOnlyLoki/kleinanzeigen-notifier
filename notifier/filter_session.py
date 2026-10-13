"""/filter: build a search by tapping through the site's own filters.

One Telegram message is edited in place as the user moves through it:
category -> keywords -> location -> radius -> overview of the filters the
results page offers (see notifier/facets.py) -> options of one filter ->
... -> Done, which hands the finished search URL to on_done (the /addurl
flow asks for name and interval from there, /edit replaces a watch's URL).

Every applied filter is a page load on the site, so the session holds the
current FacetPage plus a history of URLs for undo; facet pages are cached,
so going back is instant.

callback_data is limited to 64 bytes, so buttons carry short indices into
the current view ("o:3:12" = option 12 of group 3) plus a session token,
never URLs - a tap on a stale menu (old token) is rejected.
"""

from __future__ import annotations

import asyncio
import html
import secrets
import time
from typing import Awaitable, Callable, Dict, List, Optional

from loguru import logger

from notifier.facets import (
    CATEGORY_KEY,
    FacetGroup,
    FacetPage,
    current_price,
    parse_range,
    range_value,
    search_state,
    selected_values,
    start_url,
    toggle_value,
    with_attribute,
    with_price,
)
from notifier.search_url import _format_attr_value, describe_search_url
from notifier.telegram import Keyboard, TelegramClient

SESSION_TIMEOUT = 15 * 60
OPTIONS_PER_PAGE = 10

RADII = (5, 10, 20, 30, 50, 100, 150, 200)

_RANGE_HINT = "Send a range: <code>2008-2015</code>, <code>2008-</code> (from) or <code>-150000</code> (up to)."


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _fmt_count(count: Optional[int]) -> str:
    return "" if count is None else f" ({_thousands(count)})"


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class FilterSession:
    def __init__(
        self,
        chat_id: str,
        telegram: TelegramClient,
        load: Callable[[str], Awaitable[FacetPage]],
        on_done: Callable[[str], Awaitable[None]],
        category_labels: Dict[int, str],
        category_aliases: Dict[str, int],
        title: str = "New search",
        initial_url: Optional[str] = None,
    ):
        self.chat_id = chat_id
        self.token = secrets.token_hex(3)
        self.done = False
        self._telegram = telegram
        self._load = load
        self._on_done = on_done
        self._category_labels = category_labels
        self._category_aliases = category_aliases
        self._title = title
        self._initial_url = initial_url

        self._message_id: Optional[int] = None
        self._lock = asyncio.Lock()
        self._last_active = time.monotonic()

        # setup steps
        self._category_id: Optional[int] = None
        self._keywords: Optional[str] = None
        self._location: Optional[str] = None
        # filter steps
        self._page: Optional[FacetPage] = None
        self._history: List[str] = []
        self._view = "category"  # category|keywords|location|radius|overview|group|error
        self._group_key: Optional[str] = None
        self._group_page = 0
        self._awaiting: Optional[str] = None  # category|keywords|location|range:<key>|price
        self._failed_url: Optional[str] = None

    # ── state seen by the command handler ────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def expired(self) -> bool:
        return time.monotonic() - self._last_active > SESSION_TIMEOUT

    @property
    def awaiting_text(self) -> bool:
        return self._awaiting is not None

    # ── entry points ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        async with self._lock:
            if self._initial_url:
                self._message_id = await self._telegram.send_message(
                    self.chat_id, "⏳ Loading filters…"
                )
                await self._go(self._initial_url, record=False)
            else:
                self._view, self._awaiting = "category", "category"
                text, keyboard = self._render()
                self._message_id = await self._telegram.send_message(self.chat_id, text, keyboard)

    async def handle_callback(self, action: str) -> None:
        async with self._lock:
            self._last_active = time.monotonic()
            try:
                await self._on_callback(action)
            except Exception:
                logger.exception(f"/filter action {action!r} failed")
                await self._show_error("Something went wrong.")

    async def handle_text(self, text: str) -> None:
        async with self._lock:
            self._last_active = time.monotonic()
            try:
                await self._on_text(text.strip())
            except Exception:
                logger.exception("/filter text input failed")
                await self._show_error("Something went wrong.")

    async def cancel(self, note: str = "Cancelled.") -> None:
        self.done = True
        if self._message_id is not None:
            await self._telegram.edit_message(self.chat_id, self._message_id, note)

    # ── input handling ───────────────────────────────────────────────────────

    async def _on_callback(self, action: str) -> None:
        kind, _, arg = action.partition(":")
        page = self._page

        if kind == "q":
            await self.cancel()
            return
        if kind == "c":
            self._category_id = int(arg)
            await self._ask("keywords")
        elif kind == "skip":
            await self._ask("location" if self._view == "keywords" else "radius_done")
        elif kind == "r":
            await self._start_search(radius=int(arg) or None)
        elif kind == "g" and page:
            group = page.groups[int(arg)]
            # Nothing to pick in a range group without presets - type it in
            self._awaiting = None if group.options else f"range:{group.key}"
            self._view, self._group_key, self._group_page = "group", group.key, 0
            await self._show()
        elif kind == "p" and page:
            self._group_page = int(arg)
            await self._show()
        elif kind == "o" and page:
            group_index, option_index = map(int, arg.split(":"))
            await self._toggle(page.groups[group_index], option_index)
        elif kind == "rg" and page:
            group = page.groups[int(arg)]
            self._awaiting = f"range:{group.key}"
            await self._show()
        elif kind == "pr" and page:
            self._view, self._awaiting = "overview", "price"
            await self._show()
        elif kind == "x" and page:
            group = page.groups[int(arg)]
            await self._apply(with_attribute(page.url, group.key, None))
        elif kind == "xp" and page:
            await self._apply(with_price(page.url, None, None))
        elif kind == "b":
            self._view, self._awaiting = "overview", None
            await self._show()
        elif kind == "u" and self._history:
            await self._go(self._history.pop(), record=False)
        elif kind == "retry" and self._failed_url:
            await self._go(self._failed_url)
        elif kind == "d":
            await self._finish()

    async def _on_text(self, text: str) -> None:
        awaiting = self._awaiting
        if awaiting == "category":
            category_id = int(text) if text.isdigit() else self._category_aliases.get(text.lower())
            if not category_id:
                await self._show(note="Unknown category - tap one or send its numeric id.")
                return
            self._category_id = category_id
            await self._ask("keywords")
        elif awaiting == "keywords":
            self._keywords = None if text == "-" else text
            await self._ask("location")
        elif awaiting == "location":
            if text == "-":
                await self._start_search(radius=None)
                return
            self._location = text
            await self._ask("radius")
        elif awaiting == "price" or (awaiting or "").startswith("range:"):
            parsed = parse_range(text)
            if parsed is None:
                await self._show(note=f"Couldn't read that. {_RANGE_HINT}")
                return
            low, high = parsed
            if awaiting == "price":
                url = with_price(self._page.url, low, high)
            else:
                url = with_attribute(self._page.url, awaiting[6:], range_value(low, high))
            self._awaiting = None
            await self._apply(url)

    async def _ask(self, step: str) -> None:
        if step == "radius_done":  # skipped the location
            await self._start_search(radius=None)
            return
        if step == "radius" and not self._location:
            await self._start_search(radius=None)
            return
        self._view = step
        self._awaiting = step if step in ("keywords", "location") else None
        await self._show()

    async def _start_search(self, radius: Optional[int]) -> None:
        self._awaiting = None
        await self._go(start_url(self._category_id, self._keywords, self._location, radius))

    async def _toggle(self, group: FacetGroup, option_index: int) -> None:
        option = group.options[option_index]
        current = search_state(self._page.url).attrs.get(group.key)
        if group.key == CATEGORY_KEY or group.is_range or current is None:
            # The site's own link - exactly what tapping it there would do
            url = option.url
        else:
            url = with_attribute(self._page.url, group.key, toggle_value(current, option.value))
        await self._apply(url, stay_in_group=group.key)

    async def _apply(self, url: Optional[str], stay_in_group: Optional[str] = None) -> None:
        if url is None:
            await self._show(note="That filter can't be applied to this search.")
            return
        await self._go(url, stay_in_group=stay_in_group)

    async def _go(self, url: str, record: bool = True, stay_in_group: Optional[str] = None) -> None:
        if self._message_id is not None:
            await self._telegram.edit_message(self.chat_id, self._message_id, "⏳ Loading filters…")
        try:
            page = await self._load(url)
        except Exception:
            logger.exception(f"/filter couldn't load {url}")
            self._failed_url = url
            await self._show_error("Couldn't load the search from kleinanzeigen.de.")
            return

        if record and self._page and self._page.url != page.url:
            self._history.append(self._page.url)
        self._page = page
        self._awaiting = None
        if stay_in_group and self._has_more_options(page, stay_in_group):
            # Stay put for picking more values of the same filter
            self._view, self._group_key = "group", stay_in_group
        else:
            self._view = "overview"
        await self._show()

    @staticmethod
    def _has_more_options(page: FacetPage, key: str) -> bool:
        """Whether the site still offers values beyond those already chosen
        (e.g. more fuel types - but not more brands once "Modell" took over)."""
        group = page.group(key)
        if group is None:
            return False
        chosen = selected_values(search_state(page.url).attrs.get(key))
        return any(o.value not in chosen for o in group.options)

    async def _finish(self) -> None:
        if not self._page:
            return
        self.done = True
        summary = " · ".join(describe_search_url(self._page.url, self._category_labels))
        await self._telegram.edit_message(
            self.chat_id, self._message_id, f"✅ Search ready: {html.escape(summary) or 'all listings'}"
        )
        await self._on_done(self._page.url)

    # ── rendering ────────────────────────────────────────────────────────────

    async def _show(self, note: str = "") -> None:
        text, keyboard = self._render()
        if note:
            text = f"{text}\n\n⚠️ {note}"
        await self._telegram.edit_message(self.chat_id, self._message_id, text, keyboard)

    async def _show_error(self, message: str) -> None:
        self._view = "error"
        await self._show(note=message)

    def _cb(self, action: str) -> str:
        return f"{self.token}|{action}"

    def _render(self):
        header = f"<b>\U0001F50E {html.escape(self._title)}</b>"
        cancel = (("✖ Cancel", self._cb("q")),)

        if self._view == "category":
            buttons = [(label, self._cb(f"c:{cid}")) for cid, label in self._category_labels.items()]
            rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
            return (
                f"{header}\nPick a category - or send a kleinanzeigen.de category id.",
                rows + [list(cancel)],
            )
        if self._view == "keywords":
            return (
                f"{header}\nSearch keywords? Send them, or skip.",
                [[("Skip", self._cb("skip")), *cancel]],
            )
        if self._view == "location":
            return (
                f"{header}\nLocation - postal code or place? Send it, or skip for all of Germany.",
                [[("Skip", self._cb("skip")), *cancel]],
            )
        if self._view == "radius":
            buttons = [(f"{km} km", self._cb(f"r:{km}")) for km in RADII]
            return (
                f"{header}\nRadius around {html.escape(self._location or '')}?",
                [buttons[:4], buttons[4:], [("Only this place", self._cb("r:0")), *cancel]],
            )
        if self._view == "error":
            rows: Keyboard = []
            if self._failed_url:
                rows.append([("\U0001F504 Retry", self._cb("retry"))])
            if self._page:
                rows.append([("↩ Back to filters", self._cb("b")), ("✅ Done", self._cb("d"))])
            rows.append(list(cancel))
            return header, rows
        if self._view == "group" and self._page and self._page.group(self._group_key):
            return self._render_group(header)
        return self._render_overview(header)

    def _render_overview(self, header: str):
        page = self._page
        state = search_state(page.url)
        summary = " · ".join(describe_search_url(page.url, self._category_labels))
        lines = [header, html.escape(summary) or "All listings"]
        if page.total_results is not None:
            lines.append(f"\U0001F4CA {_thousands(page.total_results)} results")

        rows: Keyboard = []
        active = []
        for i, group in enumerate(page.groups):
            if group.key in state.attrs:
                value = _format_attr_value(group.key, state.attrs[group.key])
                active.append((_short(f"✖ {group.title}: {value}"), self._cb(f"x:{i}")))
        low, high = current_price(page.url)
        if low is not None or high is not None:
            active.append((f"✖ Preis: {low or 0}-{high or '∞'}€", self._cb("xp")))
        rows += [active[i : i + 2] for i in range(0, len(active), 2)]

        groups = [(_short(f"{g.title} ▸", 30), self._cb(f"g:{i}")) for i, g in enumerate(page.groups)]
        groups.append(("\U0001F4B6 Preis ▸", self._cb("pr")))
        rows += [groups[i : i + 3] for i in range(0, len(groups), 3)]

        bottom = [("✅ Done", self._cb("d")), ("✖ Cancel", self._cb("q"))]
        if self._history:
            bottom.insert(0, ("↩ Undo", self._cb("u")))
        rows.append(bottom)

        if self._awaiting == "price":
            lines.append(f"\n\U0001F4B6 Price in €? {_RANGE_HINT}")
        elif not page.groups:
            lines.append(
                "\nNo filters found on this page. You can still finish here - or set the "
                "search up on kleinanzeigen.de and paste its URL."
            )
        else:
            lines.append("\nTap a filter to set it, ✖ to remove one.")
        return "\n".join(lines), rows

    def _render_group(self, header: str):
        page = self._page
        index = next(i for i, g in enumerate(page.groups) if g.key == self._group_key)
        group = page.groups[index]
        selected = selected_values(search_state(page.url).attrs.get(group.key))

        pages = max(1, -(-len(group.options) // OPTIONS_PER_PAGE))
        current = min(self._group_page, pages - 1)
        start = current * OPTIONS_PER_PAGE
        buttons = []
        for offset, option in enumerate(group.options[start : start + OPTIONS_PER_PAGE]):
            mark = "✅ " if option.value in selected else ""
            buttons.append(
                (
                    _short(f"{mark}{option.label}{_fmt_count(option.count)}"),
                    self._cb(f"o:{index}:{start + offset}"),
                )
            )
        rows: Keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

        nav = []
        if current > 0:
            nav.append(("◀", self._cb(f"p:{current - 1}")))
        if current < pages - 1:
            nav.append(("▶", self._cb(f"p:{current + 1}")))
        if nav:
            rows.append(nav)
        if group.is_range and group.options:
            rows.append([("✏️ Enter range", self._cb(f"rg:{index}"))])
        rows.append([("↩ Back", self._cb("b"))])

        lines = [header, f"<b>{html.escape(group.title)}</b>"]
        if pages > 1:
            lines[-1] += f" (page {current + 1}/{pages})"
        if self._awaiting == f"range:{group.key}":
            lines.append(_RANGE_HINT)
        elif group.options:
            lines.append("Tap to select or deselect.")
        return "\n".join(lines), rows
