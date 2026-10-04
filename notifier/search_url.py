"""Watches defined by a kleinanzeigen.de search URL.

Instead of re-implementing every category's filters (cars alone have brand,
model, first registration, mileage, fuel, gearbox, body type, ...), the user
sets the search up on kleinanzeigen.de itself and hands the bot the
resulting URL. All filters are encoded in it, so the scraper just replays it -
every category and every filter works without per-category code here.

This module only validates/normalizes such URLs and turns them into a short
human-readable summary for /list; it never needs to understand a filter to
scrape it.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from utils.parse_kleinanzeigen_url import parse_kleinanzeigen_url

# The lookbehind stops a match inside some other URL (".../kleinanzeigen.de/...").
_URL_RE = re.compile(
    r"(?<![\w./-])(?:https?://)?(?:www\.|m\.)?kleinanzeigen\.de/\S+", re.IGNORECASE
)

# Optional friendlier names for common attribute keys (the part between the
# category prefix and the type suffix, e.g. autos.fuel_s -> "fuel"). Anything
# not listed is shown under its raw key, so this never needs to be complete.
_ATTR_LABELS = {
    "marke": "Marke",
    "modell": "Modell",
    "ez": "EZ",
    "km": "km",
    "fuel": "Kraftstoff",
    "shift": "Getriebe",
    "typ": "Typ",
    "art": "Art",
    "power": "Leistung",
    "condition": "Zustand",
}


def extract_search_url(text: str) -> Optional[str]:
    """Find a kleinanzeigen.de search URL in text (e.g. a message shared from
    the browser, which may carry extra words) and return it normalized to
    https://www.kleinanzeigen.de/..., or None if there is none.

    Only search/category pages (/s-...) are accepted - not single listings
    (/s-anzeige/...), and never another host, since the URL is loaded by the
    scraper's browser.
    """
    match = _URL_RE.search(text or "")
    if not match:
        return None

    raw = match.group(0).rstrip(".,;:!?")
    # A trailing ")" is part of the URL for multi-value filters like
    # typ_s:(kombi,suv) - only drop it when it closes nothing in the URL.
    while raw.endswith(")") and raw.count(")") > raw.count("("):
        raw = raw[:-1]

    if not raw.lower().startswith("http"):
        raw = "https://" + raw
    parts = urlsplit(raw)
    if not parts.hostname or not parts.hostname.lower().endswith("kleinanzeigen.de"):
        return None
    if not parts.path.startswith("/s-") or parts.path.startswith("/s-anzeige/"):
        return None

    url = f"https://www.kleinanzeigen.de{parts.path}"
    if parts.query:
        url += f"?{parts.query}"
    return url


def _format_attr_value(key: str, value: str) -> str:
    if value.startswith("(") and value.endswith(")"):
        return ", ".join(value[1:-1].split(","))
    if key.endswith("_i") and "," in value:
        low, high = value.split(",", 1)
        if low and high:
            return f"{low}–{high}"
        if low:
            return f"ab {low}"
        if high:
            return f"bis {high}"
    return value


def _attr_label(key: str) -> str:
    name = key.split(".", 1)[-1]
    name = re.sub(r"_[a-z]$", "", name)
    return _ATTR_LABELS.get(name, name)


def _path_words(parsed: Dict) -> List[str]:
    words = [parsed.get("subcategory"), parsed.get("path_keyword")]
    return [w for w in words if w and ":" not in w]


def describe_search_url(url: str, category_labels: Dict[int, str]) -> List[str]:
    """Short human-readable pieces describing the search, e.g.
    ["volkswagen klima", "Auto", "Marke: volkswagen", "EZ: ab 2008"]."""
    parsed = parse_kleinanzeigen_url(url)
    parts: List[str] = []

    words = [parsed["query"]] if parsed.get("query") else _path_words(parsed)
    if words:
        parts.append(" ".join(words))

    category_id = parsed.get("category_id")
    if category_id in category_labels:
        parts.append(category_labels[category_id])
    elif parsed.get("category_slug"):
        parts.append(parsed["category_slug"][2:])
    elif category_id:
        parts.append(f"c{category_id}")

    if parsed.get("location"):
        parts.append(parsed["location"])
    if parsed.get("radius"):
        parts.append(f"+{parsed['radius']}km")
    if parsed.get("min_price") is not None or parsed.get("max_price") is not None:
        parts.append(f"{parsed.get('min_price') or 0}-{parsed.get('max_price') or '∞'}€")

    for key, value in parsed.get("attributes", {}).items():
        parts.append(f"{_attr_label(key)}: {_format_attr_value(key, value)}")

    return parts


def suggest_watch_name(url: str, category_labels: Dict[int, str]) -> str:
    parsed = parse_kleinanzeigen_url(url)
    if parsed.get("query"):
        return parsed["query"]
    words = _path_words(parsed)
    if words:
        return " ".join(dict.fromkeys(w.replace("-", " ") for w in words)).title()
    if parsed.get("category_id") in category_labels:
        return category_labels[parsed["category_id"]]
    return "Suche"
