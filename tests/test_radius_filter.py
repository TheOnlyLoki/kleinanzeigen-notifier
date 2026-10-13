"""
Unit tests for dropping listings outside a watch's radius (notifier/geo.py).

Run with:
  pytest tests/test_radius_filter.py -v
"""

import asyncio
import io
import zipfile

import httpx
import pytest

import notifier.geo as geo
import notifier.watchers as watchers
from notifier.config import WatchConfig
from notifier.geo import PostalGeo, SearchArea, split_by_radius
from notifier.search_url import url_search_area
from notifier.storage import SeenAdsStore

# GeoNames postal code dump format (tab-separated, 12 columns)
GEONAMES_ROWS = [
    ("38100", "Braunschweig", 52.2647, 10.5237),
    ("38102", "Braunschweig", 52.2556, 10.5390),
    ("38300", "Wolfenbüttel", 52.1620, 10.5370),  # ~12 km from 38100
    ("01067", "Dresden", 51.0577, 13.7210),  # ~250 km
    ("10115", "Berlin", 52.5323, 13.3846),  # ~195 km
    ("80331", "München", 48.1374, 11.5755),
    ("01844", "Neustadt", 51.0240, 14.2140),  # two far-apart "Neustadt"s
    ("96465", "Neustadt", 50.3290, 11.1210),
]
GEONAMES_TEXT = "".join(
    f"DE\t{plz}\t{place}\tLand\tXX\t\t\t\t\t{lat}\t{lon}\t4\n"
    for plz, place, lat, lon in GEONAMES_ROWS
)


@pytest.fixture
def postal_geo():
    return PostalGeo.parse(GEONAMES_TEXT.splitlines(keepends=True))


@pytest.fixture(autouse=True)
def reset_geo_cache(monkeypatch):
    monkeypatch.setattr(geo, "_geo", None)
    monkeypatch.setattr(geo, "_last_failure", 0.0)


# ── lookups ──────────────────────────────────────────────────────────────────


def test_locate_by_postal_code_place_and_slug(postal_geo):
    assert postal_geo.locate("38100") == pytest.approx((52.2647, 10.5237))
    assert postal_geo.locate("38300 Wolfenbüttel") == pytest.approx((52.1620, 10.5370))
    braunschweig = postal_geo.locate("Braunschweig")
    assert braunschweig == pytest.approx((52.26015, 10.53135))
    assert postal_geo.locate("braunschweig") == braunschweig
    assert postal_geo.locate("muenchen") == postal_geo.locate("München")


def test_locate_unknown_and_ambiguous(postal_geo):
    assert postal_geo.locate("99999") is None
    assert postal_geo.locate("Atlantis") is None
    assert postal_geo.locate("Neustadt") is None  # centroid would be meaningless
    assert postal_geo.locate("") is None


# ── filtering ────────────────────────────────────────────────────────────────


def _listing(adid, location):
    return {"adid": adid, "location": location, "title": adid, "price": "100", "url": ""}


def test_split_by_radius_postal_codes(postal_geo):
    area = SearchArea(radius_km=30, center=postal_geo.locate("38100"))
    listings = [
        _listing("near", "38300 Wolfenbüttel"),
        _listing("dresden", "01067 Dresden"),
        _listing("berlin", "10115 Berlin Mitte"),
        _listing("unknown-plz", "99999 Nirgendwo"),
        _listing("no-plz", "Niedersachsen"),
    ]
    kept, dropped = split_by_radius(listings, area, postal_geo)
    assert [l["adid"] for l in kept] == ["near", "unknown-plz", "no-plz"]
    assert [l["adid"] for l, _ in dropped] == ["dresden", "berlin"]
    dresden_km, berlin_km = (d for _, d in dropped)
    assert 240 < dresden_km < 270 and 180 < berlin_km < 210


def test_split_by_radius_prefers_stated_distance(postal_geo):
    # Stated distances work even without a center or postal code data
    area = SearchArea(radius_km=20)
    listings = [
        _listing("ok", "38300 Wolfenbüttel (12 km)"),
        _listing("tolerance", "Somewhere (24 km)"),  # 20 km + 5 km slack
        _listing("far", "01067 Dresden (251 km)"),
        _listing("decimal-far", "Ort (30,5 km)"),
        _listing("unknown", "01067 Dresden"),  # no center -> can't tell -> keep
    ]
    kept, dropped = split_by_radius(listings, area, None)
    assert [l["adid"] for l in kept] == ["ok", "tolerance", "unknown"]
    assert [l["adid"] for l, _ in dropped] == ["far", "decimal-far"]


def test_url_search_area():
    candidates, radius = url_search_area(
        "https://www.kleinanzeigen.de/s-autos/braunschweig/subaru-impreza/k0c216l2911r20"
    )
    assert radius == 20
    assert candidates[0] == "braunschweig"

    candidates, radius = url_search_area(
        "https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=subaru&locationStr=38100&radius=30"
    )
    assert (candidates[0], radius) == ("38100", 30)

    assert url_search_area("https://www.kleinanzeigen.de/s-autos/subaru/k0c216")[1] is None


# ── run_watch_once ───────────────────────────────────────────────────────────


RESULTS = [
    _listing("1", "38300 Wolfenbüttel"),
    _listing("2", "01067 Dresden"),
    _listing("3", "10115 Berlin"),
]


def _run_watch(monkeypatch, tmp_path, postal_geo, watch):
    async def fake_scrape(**kwargs):
        return {"success": True, "results": [dict(r) for r in RESULTS]}

    async def fake_geo():
        return postal_geo

    monkeypatch.setattr(watchers, "ultra_optimized_scrape_inserate", fake_scrape)
    monkeypatch.setattr(watchers, "scrape_by_url", fake_scrape)
    monkeypatch.setattr(watchers, "get_postal_geo", fake_geo)
    store = SeenAdsStore(watch.name, data_dir=tmp_path)
    events = asyncio.run(watchers.run_watch_once(None, watch, store))
    return [e.listing["adid"] for e in events], store


def test_watch_drops_far_listings_and_never_stores_them(monkeypatch, tmp_path, postal_geo):
    watch = WatchConfig(name="Subaru", query="subaru impreza", location="38100", radius=30)
    adids, store = _run_watch(monkeypatch, tmp_path, postal_geo, watch)
    assert adids == ["1"]
    assert store.get("2") is None and store.get("3") is None


def test_url_watch_uses_location_slug(monkeypatch, tmp_path, postal_geo):
    watch = WatchConfig(
        name="Subaru URL",
        url="https://www.kleinanzeigen.de/s-autos/braunschweig/subaru-impreza/k0c216l2911r20",
    )
    adids, _ = _run_watch(monkeypatch, tmp_path, postal_geo, watch)
    assert adids == ["1"]


def test_watch_without_radius_keeps_everything(monkeypatch, tmp_path, postal_geo):
    watch = WatchConfig(name="Bundesweit", query="subaru impreza")
    adids, _ = _run_watch(monkeypatch, tmp_path, postal_geo, watch)
    assert adids == ["1", "2", "3"]


# ── loading / download ───────────────────────────────────────────────────────


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("DE.txt", GEONAMES_TEXT)
        archive.writestr("readme.txt", "GeoNames")
    return buf.getvalue()


def _mock_httpx(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        geo.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


def test_downloads_once_then_uses_cached_file(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, content=_zip_bytes())

    _mock_httpx(monkeypatch, handler)
    path = tmp_path / "geo" / "DE.txt"

    loaded = asyncio.run(geo.get_postal_geo(path))
    assert loaded.locate("38100") is not None
    assert path.exists() and len(calls) == 1

    monkeypatch.setattr(geo, "_geo", None)  # fresh process, file already on disk
    assert asyncio.run(geo.get_postal_geo(path)).locate("01067") is not None
    assert len(calls) == 1


def test_download_failure_is_not_retried_immediately(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503)

    _mock_httpx(monkeypatch, handler)
    path = tmp_path / "geo" / "DE.txt"

    assert asyncio.run(geo.get_postal_geo(path)) is None
    assert asyncio.run(geo.get_postal_geo(path)) is None
    assert len(calls) == 1
