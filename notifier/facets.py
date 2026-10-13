"""Read a search page's own filter options ("facets") and edit search URLs.

Used by /filter (notifier/filter_session.py) to offer every filter of every
category as Telegram buttons without hard-coding any of them: the results
page's sidebar links each apply one filter (e.g. Marke "BMW (30.118)" links to
.../k0c216+autos.marke_s:bmw). A link whose URL adds or changes exactly one
filter attribute compared to the current search *is* a filter option - that
needs no knowledge of the page's markup or class names, which change often.

Split in two so the logic is testable without a browser:
- _COLLECT_JS (in the page) only dumps raw links / input names / result count
- build_facet_page() (plain Python) turns that into groups of options
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from notifier.search_url import _attr_label
from scrapers.inserate_by_url import _parse_breadcrumb
from scrapers.inserate_ultra_optimized import _block_unneeded_resources

BASE_URL = "https://www.kleinanzeigen.de"
SEARCH_FORM_URL = f"{BASE_URL}/s-suchanfrage.html"

CATEGORY_KEY = "category"
PRICE_KEY = "price"

# k0c216l2911r20 / c216 (the k<n> prefix and location/radius are optional)
_FILTER_SEGMENT_RE = re.compile(r"^(k\d+)?c(\d+)(?:l(\d+))?(?:r(\d+))?$")
_COUNT_RE = re.compile(r"\(\s*([\d.]+)\s*\)")
_RANGE_INPUT_RE = re.compile(r"([a-z0-9_]+\.[a-z0-9_]+?_i)(?![a-z0-9])")


# ── URL state ────────────────────────────────────────────────────────────────


@dataclass
class SearchState:
    """The filter-relevant parts of a search URL."""

    category_id: Optional[int] = None
    location_id: Optional[str] = None
    radius: Optional[str] = None
    attrs: Dict[str, str] = field(default_factory=dict)  # "autos.marke_s" -> "bmw"
    # False for search-form URLs (/s-suchanfrage.html?...), which can't
    # carry attributes and whose location isn't resolved yet
    canonical: bool = True


def _path_segments(url: str) -> List[str]:
    return [s for s in unquote(urlsplit(url).path).split("/") if s]


def _filter_index(segments: List[str]) -> Optional[int]:
    # The last match: the filter segment ends the path, while a keyword
    # segment before it can look the same (e.g. "c3" for a Citroën C3).
    for i in range(len(segments) - 1, -1, -1):
        if _FILTER_SEGMENT_RE.match(segments[i].split("+", 1)[0]):
            return i
    return None


def search_state(url: str) -> SearchState:
    parts = urlsplit(url)
    segments = _path_segments(url)
    idx = _filter_index(segments)
    if idx is None:
        # Search-form URL (/s-suchanfrage.html?categoryId=216&...)
        query = dict(parse_qsl(parts.query))
        category = query.get("categoryId")
        return SearchState(
            category_id=int(category) if category and category.isdigit() else None,
            canonical=False,
        )

    head, *attrs = segments[idx].split("+")
    match = _FILTER_SEGMENT_RE.match(head)
    state = SearchState(
        category_id=int(match.group(2)), location_id=match.group(3), radius=match.group(4)
    )
    for attr in attrs:
        key, sep, value = attr.partition(":")
        if sep:
            state.attrs[key] = value
    return state


def _encode_value(value: str) -> str:
    # Matches the site's own URLs: commas escaped, parentheses literal.
    return quote(value, safe="()-_.")


def _build_path(segments: List[str]) -> str:
    idx = _filter_index(segments)
    out = []
    for i, seg in enumerate(segments):
        if i == idx:
            head, *attrs = seg.split("+")
            pairs = [a.split(":", 1) for a in attrs if ":" in a]
            seg = "+".join([head] + [f"{k}:{_encode_value(v)}" for k, v in pairs])
        else:
            seg = quote(seg, safe=":")
        out.append(seg)
    return "/" + "/".join(out)


def with_attribute(url: str, key: str, value: Optional[str]) -> Optional[str]:
    """url with filter attribute key set to value (None removes it). None if
    the URL has no filter segment to edit (e.g. a search-form URL)."""
    parts = urlsplit(url)
    segments = _path_segments(url)
    idx = _filter_index(segments)
    if idx is None:
        return None

    head, *raw_attrs = segments[idx].split("+")
    attrs = dict(a.split(":", 1) for a in raw_attrs if ":" in a)
    if value is None:
        attrs.pop(key, None)
    else:
        attrs[key] = value

    # Attributes in alphabetical order, as the site generates them
    segments[idx] = "+".join([head] + [f"{k}:{v}" for k, v in sorted(attrs.items())])
    return urlunsplit(parts._replace(path=_build_path(segments)))


def with_price(url: str, min_price: Optional[int], max_price: Optional[int]) -> str:
    parts = urlsplit(url)
    segments = _path_segments(url)
    if _filter_index(segments) is None:
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("minPrice", "maxPrice")]
        if min_price is not None:
            query.append(("minPrice", str(min_price)))
        if max_price is not None:
            query.append(("maxPrice", str(max_price)))
        return urlunsplit(parts._replace(query=urlencode(query)))

    segments = [s for s in segments if not s.startswith("preis:")]
    if min_price is not None or max_price is not None:
        # Right after the category slug, as in the site's own URLs
        insert_at = 1 if segments and segments[0].startswith("s-") else 0
        price = f"preis:{'' if min_price is None else min_price}:{'' if max_price is None else max_price}"
        segments.insert(insert_at, price)
    return urlunsplit(parts._replace(path=_build_path(segments)))


def current_price(url: str) -> Tuple[Optional[int], Optional[int]]:
    for seg in _path_segments(url):
        if seg.startswith("preis:"):
            _, low, high = (seg.split(":") + ["", ""])[:3]
            return (int(low) if low.isdigit() else None, int(high) if high.isdigit() else None)
    query = dict(parse_qsl(urlsplit(url).query))
    low, high = query.get("minPrice", ""), query.get("maxPrice", "")
    return (int(low) if low.isdigit() else None, int(high) if high.isdigit() else None)


def selected_values(value: Optional[str]) -> List[str]:
    if not value:
        return []
    if value.startswith("(") and value.endswith(")"):
        return [v for v in value[1:-1].split(",") if v]
    return [value]


def toggle_value(current: Optional[str], value: str) -> Optional[str]:
    """Add/remove one value of a (possibly multi-value) attribute:
    None + "lpg" -> "lpg"; "lpg" + "cng" -> "(lpg,cng)"; "(lpg,cng)" - "cng" -> "lpg"."""
    values = selected_values(current)
    if value in values:
        values.remove(value)
    else:
        values.append(value)
    if not values:
        return None
    return values[0] if len(values) == 1 else f"({','.join(values)})"


_RANGE_TEXT_RE = re.compile(r"^\s*(\d*)\s*[-–]\s*(\d*)\s*$")


def parse_range(text: str) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """"2008-2015" / "2008-" / "-150000" / "2008" (= from) -> (low, high)."""
    text = text.replace(".", "").replace(" ", "")
    if text.isdigit():
        return int(text), None
    match = _RANGE_TEXT_RE.match(text)
    if not match or not (match.group(1) or match.group(2)):
        return None
    low = int(match.group(1)) if match.group(1) else None
    high = int(match.group(2)) if match.group(2) else None
    if low is not None and high is not None and low > high:
        low, high = high, low
    return low, high


def range_value(low: Optional[int], high: Optional[int]) -> str:
    """(2008, None) -> "2008," - the site's format for *_i range attributes."""
    return f"{'' if low is None else low},{'' if high is None else high}"


def start_url(
    category_id: Optional[int],
    keywords: Optional[str] = None,
    location: Optional[str] = None,
    radius: Optional[int] = None,
) -> str:
    """Search-form URL; the site redirects it to the canonical search URL
    (location resolved to its id), which is what filters are then added to."""
    params: List[Tuple[str, str]] = []
    if keywords:
        params.append(("keywords", keywords))
    if category_id:
        params.append(("categoryId", str(category_id)))
    if location:
        params.append(("locationStr", location))
        if radius:
            params.append(("radius", str(radius)))
    return f"{SEARCH_FORM_URL}?{urlencode(params)}"


# ── facets ───────────────────────────────────────────────────────────────────


@dataclass
class FacetOption:
    label: str
    value: str  # attribute value, or category id for the category group
    url: str  # the site's own link applying this option
    count: Optional[int] = None


@dataclass
class FacetGroup:
    key: str  # "autos.marke_s", CATEGORY_KEY, PRICE_KEY
    title: str
    options: List[FacetOption] = field(default_factory=list)
    is_range: bool = False


@dataclass
class FacetPage:
    url: str
    groups: List[FacetGroup]
    total_results: Optional[int] = None

    def group(self, key: str) -> Optional[FacetGroup]:
        return next((g for g in self.groups if g.key == key), None)


def _clean_label(text: str) -> str:
    label = _COUNT_RE.sub("", text or "")
    return " ".join(label.split())


def _parse_count(*texts: str) -> Optional[int]:
    for text in texts:
        match = _COUNT_RE.search(text or "")
        if match:
            return int(match.group(1).replace(".", ""))
    return None


def _group_title(key: str, heading: str) -> str:
    heading = " ".join((heading or "").split())
    return heading if heading and len(heading) <= 30 else _attr_label(key)


def build_facet_page(page_url: str, raw: Dict[str, Any]) -> FacetPage:
    """Turn _COLLECT_JS output into option groups (see module docstring)."""
    current = search_state(page_url)
    groups: "OrderedDict[str, FacetGroup]" = OrderedDict()

    for link in raw.get("links", []):
        href = urljoin(BASE_URL, link.get("href") or "")
        path = urlsplit(href).path
        if urlsplit(href).hostname not in ("www.kleinanzeigen.de", "kleinanzeigen.de"):
            continue
        if not path.startswith("/s-") or path.startswith("/s-anzeige/"):
            continue

        option = search_state(href)
        if option.category_id is None:
            continue
        # Location/radius come from the start of /filter, never from a link
        if current.canonical and (
            option.location_id != current.location_id or option.radius != current.radius
        ):
            continue

        label = _clean_label(link.get("text", ""))
        if not label:
            continue
        count = _parse_count(link.get("text", ""), link.get("context", ""))

        if option.category_id != current.category_id:
            # A different category - kept only if it doesn't also add filters
            if set(option.attrs) - set(current.attrs):
                continue
            key, value = CATEGORY_KEY, str(option.category_id)
            title = "Kategorie"
        else:
            removed = set(current.attrs) - set(option.attrs)
            changed = [k for k, v in option.attrs.items() if current.attrs.get(k) != v]
            if removed or len(changed) != 1:
                continue
            key = changed[0]
            value = unquote(option.attrs[key])
            title = _group_title(key, link.get("heading", ""))

        group = groups.setdefault(key, FacetGroup(key=key, title=title, is_range=key.endswith("_i")))
        if all(o.value != value for o in group.options):
            group.options.append(FacetOption(label=label, value=value, url=href, count=count))

    # Range filters are form inputs, not links - learn their keys from the
    # inputs' names (e.g. "autos.ez_i_from"), so they can be typed in.
    for name in raw.get("inputs", []):
        match = _RANGE_INPUT_RE.search(name or "")
        if match and match.group(1) not in groups:
            key = match.group(1)
            groups[key] = FacetGroup(key=key, title=_group_title(key, ""), is_range=True)

    # Filters already applied stay listed even when the page stops offering
    # them (it typically doesn't link a value that's already selected), so
    # they can still be seen and deselected.
    for key, value in current.attrs.items():
        group = groups.get(key)
        if group is None:
            group = groups[key] = FacetGroup(
                key=key, title=_group_title(key, ""), is_range=key.endswith("_i")
            )
        if group.is_range:
            continue
        for selected in reversed(selected_values(unquote(value))):
            if all(o.value != selected for o in group.options):
                group.options.insert(
                    0, FacetOption(label=selected, value=selected, url=page_url)
                )

    total, _ = _parse_breadcrumb(raw.get("summary") or "")
    return FacetPage(url=page_url, groups=list(groups.values()), total_results=total)


# Runs in the page: raw material for build_facet_page(). Each link carries
# the nearest heading before it (the facet's title on the site) and its
# list item's text (where the site puts the result count).
_COLLECT_JS = r"""
() => {
    const links = [];
    const inputs = [];
    let heading = "";
    // querySelectorAll returns document order, so `heading` is always the
    // last heading before the current link.
    const nodes = document.querySelectorAll(
        'h2, h3, h4, h5, h6, legend, [role="heading"], a[href*="/s-"], input[name], select[name]'
    );
    for (const el of nodes) {
        if (el.tagName === "A") {
            if (el.closest("article")) continue;  // result cards link to listings
            const box = el.closest("li") || el.parentElement;
            links.push({
                href: el.getAttribute("href"),
                text: (el.innerText || "").trim(),
                context: box ? (box.innerText || "").trim().slice(0, 200) : "",
                heading: heading,
            });
            if (links.length >= 3000) break;
        } else if (el.tagName === "INPUT" || el.tagName === "SELECT") {
            inputs.push(el.getAttribute("name"));
        } else {
            heading = (el.innerText || "").trim();
        }
    }
    const summary = document.querySelector(".breadcrump-summary");
    return { links, inputs, summary: summary ? summary.innerText : "" };
}
"""


# ── loading (browser) ────────────────────────────────────────────────────────

_CACHE_SIZE = 50
_CACHE_TTL = 6 * 3600
_cache: "OrderedDict[str, Tuple[float, FacetPage]]" = OrderedDict()


def _cache_get(url: str) -> Optional[FacetPage]:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        _cache.move_to_end(url)
        return hit[1]
    _cache.pop(url, None)
    return None


def _cache_put(urls: List[str], page: FacetPage) -> None:
    for url in urls:
        _cache[url] = (time.monotonic(), page)
        _cache.move_to_end(url)
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)


async def load_facet_page(browser_manager, url: str) -> FacetPage:
    """Load url in the shared browser and read its filter options. Cached:
    going back, paging and reopening groups don't hit the site again."""
    cached = _cache_get(url)
    if cached:
        return cached

    context = await browser_manager.get_context()
    page = await context.new_page()
    try:
        await _block_unneeded_resources(page)
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        raw = await page.evaluate(_COLLECT_JS)
        final_url = page.url  # after the site's redirect to the canonical URL
    finally:
        await page.close()
        await browser_manager.release_context(context)

    facet_page = build_facet_page(final_url, raw)
    _cache_put([url, final_url], facet_page)
    return facet_page
