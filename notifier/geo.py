"""Drop listings outside a watch's search radius.

When a radius search has only a few hits, kleinanzeigen.de pads the results
page with listings from further away, which the scraper can't tell apart
from real hits by markup alone. So the distance is checked here instead:

1. If the card states one ("38100 Braunschweig (12 km)"), that's used.
2. Otherwise it's computed from the listing's postal code to the watch's
   location (postal code or place name), using GeoNames' German postal code
   centroids (https://www.geonames.org, CC BY 4.0). That file is downloaded
   once on first use and cached under data/ - if that fails, only rule 1
   applies until the next retry.

Listings whose distance can't be determined are always kept: better a stray
notification than a missed listing.
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import re
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from loguru import logger

from notifier.storage import DATA_DIR

GEONAMES_URL = os.environ.get(
    "NOTIFIER_GEONAMES_URL", "https://download.geonames.org/export/zip/DE.zip"
)
GEO_PATH = DATA_DIR / "geo" / "DE.txt"
_RETRY_SECONDS = 3600

# Slack on top of the radius: postal code centroids aren't the exact points
# kleinanzeigen.de measures from, and a few km over is not what this is about
# (the padding listings are typically hundreds of km away).
_MIN_TOLERANCE_KM = 5.0
_TOLERANCE_FACTOR = 0.15

# A place name whose postal codes spread wider than this is ambiguous
# (e.g. "Neustadt") - its centroid would be meaningless.
_MAX_PLACE_SPREAD_KM = 30.0

_DISTANCE_RE = re.compile(r"\(\s*(\d+(?:[.,]\d+)?)\s*km\s*\)", re.IGNORECASE)
_PLZ_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")

Coord = Tuple[float, float]


def haversine_km(a: Coord, b: Coord) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def normalize_place(name: str) -> str:
    """'Frankfurt am Main' / 'frankfurt-am-main' / 'München' / 'muenchen' -> one key."""
    name = name.lower().strip()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        name = name.replace(src, dst)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _centroid(points: List[Coord]) -> Coord:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


class PostalGeo:
    def __init__(self, by_plz: Dict[str, Coord], by_place: Dict[str, Coord]):
        self._by_plz = by_plz
        self._by_place = by_place

    @classmethod
    def parse(cls, lines: Iterable[str]) -> "PostalGeo":
        """GeoNames postal code dump: tab-separated, postal code in column 2,
        place name in 3, latitude/longitude in 10/11."""
        plz_points: Dict[str, List[Coord]] = defaultdict(list)
        place_points: Dict[str, List[Coord]] = defaultdict(list)
        for line in lines:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 11:
                continue
            try:
                point = (float(cols[9]), float(cols[10]))
            except ValueError:
                continue
            plz_points[cols[1]].append(point)
            place_points[normalize_place(cols[2])].append(point)

        by_plz = {plz: _centroid(pts) for plz, pts in plz_points.items()}
        by_place = {}
        for place, pts in place_points.items():
            center = _centroid(pts)
            if all(haversine_km(center, p) <= _MAX_PLACE_SPREAD_KM for p in pts):
                by_place[place] = center
        return cls(by_plz, by_place)

    def locate(self, location: str) -> Optional[Coord]:
        """A postal code ("38100", or text containing one) or a place name."""
        if not location:
            return None
        match = _PLZ_RE.search(location)
        if match:
            return self._by_plz.get(match.group(1))
        return self._by_place.get(normalize_place(location))


_geo: Optional[PostalGeo] = None
_last_failure = 0.0
_load_lock = asyncio.Lock()


async def _download(path: Path) -> None:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(GEONAMES_URL)
        resp.raise_for_status()

    def extract() -> None:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            data = archive.read("DE.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    await asyncio.to_thread(extract)


def _load(path: Path) -> PostalGeo:
    with path.open("r", encoding="utf-8") as f:
        return PostalGeo.parse(f)


async def get_postal_geo(path: Path = GEO_PATH) -> Optional[PostalGeo]:
    """The postal code table, downloading it on first use. None while it's
    unavailable (retried at most hourly, so a GeoNames outage costs nothing)."""
    global _geo, _last_failure
    if _geo is not None:
        return _geo

    async with _load_lock:
        recently_failed = _last_failure and time.monotonic() - _last_failure < _RETRY_SECONDS
        if _geo is not None or recently_failed:
            return _geo
        try:
            if not path.exists():
                logger.info(f"Downloading postal code data from {GEONAMES_URL}")
                await _download(path)
            _geo = await asyncio.to_thread(_load, path)
        except Exception:
            _last_failure = time.monotonic()
            logger.exception("Postal code data unavailable - radius check uses stated distances only")
        return _geo


@dataclass
class SearchArea:
    radius_km: int
    center: Optional[Coord] = None

    @property
    def max_distance_km(self) -> float:
        return self.radius_km + max(_MIN_TOLERANCE_KM, self.radius_km * _TOLERANCE_FACTOR)


def listing_distance_km(
    location_text: str, area: SearchArea, geo: Optional[PostalGeo]
) -> Optional[float]:
    match = _DISTANCE_RE.search(location_text or "")
    if match:
        return float(match.group(1).replace(",", "."))
    if area.center is None or geo is None:
        return None
    point = geo.locate(location_text or "")
    return haversine_km(area.center, point) if point else None


def split_by_radius(
    listings: List[Dict[str, Any]], area: SearchArea, geo: Optional[PostalGeo]
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], float]]]:
    """(kept, [(dropped listing, its distance in km)])."""
    kept, dropped = [], []
    for listing in listings:
        distance = listing_distance_km(listing.get("location", ""), area, geo)
        if distance is not None and distance > area.max_distance_km:
            dropped.append((listing, distance))
        else:
            kept.append(listing)
    return kept, dropped
