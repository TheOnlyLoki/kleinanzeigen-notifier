"""Search watcher: polls a saved search and diffs results against seen state
to detect new listings and price drops. New notification kinds plug in here
by adding another comparison + a "kind" string in WatchConfig.notify_on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from notifier.config import WatchConfig
from notifier.storage import SeenAdsStore
from scrapers.inserate_ultra_optimized import ultra_optimized_scrape_inserate
from utils.browser import OptimizedPlaywrightManager

_PRICE_RE = re.compile(r"\d+")


def parse_price(price_text: str) -> Optional[int]:
    if not price_text:
        return None
    digits = "".join(_PRICE_RE.findall(price_text))
    return int(digits) if digits else None


@dataclass
class WatchEvent:
    kind: str  # "new_listing" | "price_drop"
    watch_name: str
    listing: Dict[str, Any]
    old_price: Optional[int] = None


async def run_watch_once(
    browser_manager: OptimizedPlaywrightManager,
    watch: WatchConfig,
    store: SeenAdsStore,
) -> List[WatchEvent]:
    response = await ultra_optimized_scrape_inserate(
        browser_manager=browser_manager,
        query=watch.query,
        location=watch.location,
        radius=watch.radius,
        min_price=watch.min_price,
        max_price=watch.max_price,
        category_id=watch.category_id,
        page_count=watch.page_count,
    )

    if not response.get("success", False):
        logger.warning(f"[{watch.name}] scrape failed: {response.get('error')}")
        return []

    events: List[WatchEvent] = []

    for listing in response.get("results", []):
        adid = listing.get("adid")
        if not adid:
            continue

        new_price = parse_price(listing.get("price", ""))
        existing = store.get(adid)

        if existing is None:
            if "new_listing" in watch.notify_on:
                events.append(WatchEvent("new_listing", watch.name, listing))
            store.upsert(adid, price=new_price, title=listing.get("title"))
            continue

        old_price = existing.get("price")
        if (
            "price_drop" in watch.notify_on
            and old_price is not None
            and new_price is not None
            and new_price < old_price
        ):
            events.append(WatchEvent("price_drop", watch.name, listing, old_price=old_price))
            store.upsert(adid, price=new_price)

    await store.save()
    return events


def format_event(event: WatchEvent) -> str:
    listing = event.listing
    title = listing.get("title") or "(kein Titel)"
    price = listing.get("price") or "?"
    location = listing.get("location") or ""
    url = listing.get("url", "")

    if event.kind == "new_listing":
        header = f"\U0001F195 Neue Anzeige – {event.watch_name}"
        price_line = f"\U0001F4B6 {price}€"
    else:
        header = f"\U0001F4C9 Preissenkung – {event.watch_name}"
        price_line = f"\U0001F4B6 {event.old_price}€ → {price}€"

    return f"{header}\n{title}\n{price_line}\n\U0001F4CD {location}\n{url}"
