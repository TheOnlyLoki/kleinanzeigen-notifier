"""Owns the background per-watch polling loops, started/stopped from the
FastAPI app lifespan. Watches can be added/removed at runtime (e.g. from
Telegram commands in notifier/commands.py), not just at startup."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from loguru import logger

from notifier.config import AppConfig, WatchConfig, load_config, save_config
from notifier.storage import SeenAdsStore
from notifier.telegram import TelegramClient
from notifier.watchers import format_event, run_watch_once
from utils.browser import OptimizedPlaywrightManager


async def _watch_loop(
    browser_manager: OptimizedPlaywrightManager,
    watch: WatchConfig,
    store: SeenAdsStore,
    lock: asyncio.Lock,
    telegram: Optional[TelegramClient],
    default_chat_id: Optional[str],
) -> None:
    chat_id = watch.chat_id or default_chat_id

    while True:
        try:
            async with lock:
                events = await run_watch_once(browser_manager, watch, store)
            for event in events:
                logger.info(f"[{watch.name}] {event.kind}: {event.listing.get('title')}")
                if telegram and chat_id:
                    await telegram.send_message(chat_id, format_event(event))
                elif not chat_id:
                    logger.warning(
                        f"[{watch.name}] event triggered but no Telegram chat_id configured"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"[{watch.name}] watch loop iteration failed")

        await asyncio.sleep(watch.interval_minutes * 60)


class NotifierService:
    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stores: Dict[str, SeenAdsStore] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self.config: Optional[AppConfig] = None
        self.telegram: Optional[TelegramClient] = None
        self._browser_manager: Optional[OptimizedPlaywrightManager] = None

    def start(self, browser_manager: OptimizedPlaywrightManager) -> None:
        self._browser_manager = browser_manager
        self.config = load_config()

        if self.config.telegram_bot_token:
            self.telegram = TelegramClient(self.config.telegram_bot_token)
        else:
            logger.warning("TELEGRAM_BOT_TOKEN not set - notifications will only be logged.")

        for watch in self.config.watches:
            self._spawn(watch)

    def _spawn(self, watch: WatchConfig) -> None:
        store = SeenAdsStore(watch.name)
        lock = asyncio.Lock()
        self._stores[watch.name] = store
        self._locks[watch.name] = lock
        task = asyncio.create_task(
            _watch_loop(
                self._browser_manager,
                watch,
                store,
                lock,
                self.telegram,
                self.config.telegram_chat_id,
            ),
            name=f"watch:{watch.name}",
        )
        self._tasks[watch.name] = task
        logger.info(f"Started watch '{watch.name}' every {watch.interval_minutes}min")

    def list_watches(self) -> List[WatchConfig]:
        return list(self.config.watches) if self.config else []

    async def poll_watch(self, name: str):
        """Run one watch immediately (e.g. from /poll), sharing its seen-ads
        state with the scheduled loop so this doesn't cause duplicate
        'new_listing' events on the next scheduled run."""
        watch = next((w for w in self.config.watches if w.name == name), None)
        if watch is None:
            raise ValueError(f"No such watch: {name}")

        async with self._locks[name]:
            return await run_watch_once(self._browser_manager, watch, self._stores[name])

    async def add_watch(self, watch: WatchConfig) -> None:
        if watch.name in self._tasks:
            raise ValueError(f"Watch '{watch.name}' already exists")
        self.config.watches.append(watch)
        save_config(self.config)
        self._spawn(watch)

    async def remove_watch(self, name: str) -> bool:
        task = self._tasks.pop(name, None)
        if task is None:
            return False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.config.watches = [w for w in self.config.watches if w.name != name]
        save_config(self.config)
        self._stores.pop(name, None)
        self._locks.pop(name, None)
        return True

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
