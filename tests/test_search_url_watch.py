"""
Unit tests for URL-based watches (notifier/search_url.py, /addurl).

Run with:
  pytest tests/test_search_url_watch.py -v
"""

import asyncio

from notifier.commands import CATEGORY_LABELS, TelegramCommandHandler
from notifier.config import WatchConfig
from notifier.search_url import describe_search_url, extract_search_url, suggest_watch_name
from scrapers.inserate_by_url import inject_page
from utils.parse_kleinanzeigen_url import parse_kleinanzeigen_url

VW_URL = (
    "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.ez_i:2008%2C"
    "+autos.fuel_s:(cng%2Clpg)+autos.km_i:2%2C+autos.marke_s:volkswagen"
    "+autos.shift_s:automatik+autos.typ_s:(kombi%2Csuv)"
)


# ── extract_search_url ───────────────────────────────────────────────────────


def test_extract_plain_url():
    assert extract_search_url(VW_URL) == VW_URL


def test_extract_from_shared_text_keeps_closing_paren():
    # Multi-value filters end in ")" - must not be trimmed as punctuation
    text = f"Schau mal: {VW_URL}"
    assert extract_search_url(text) == VW_URL


def test_extract_trims_trailing_punctuation_and_unbalanced_paren():
    url = "https://www.kleinanzeigen.de/s-autos/k0c216"
    assert extract_search_url(f"({url}).") == url


def test_extract_normalizes_host_and_scheme():
    assert (
        extract_search_url("kleinanzeigen.de/s-autos/k0c216?foo=1")
        == "https://www.kleinanzeigen.de/s-autos/k0c216?foo=1"
    )
    assert (
        extract_search_url("https://m.kleinanzeigen.de/s-autos/k0c216")
        == "https://www.kleinanzeigen.de/s-autos/k0c216"
    )


def test_extract_rejects_listings_other_hosts_and_plain_text():
    assert extract_search_url("https://www.kleinanzeigen.de/s-anzeige/bmw/123-216-1") is None
    assert extract_search_url("https://www.kleinanzeigen.de/m-meine-anzeigen.html") is None
    assert extract_search_url("https://evil.example/kleinanzeigen.de/s-autos") is None
    assert extract_search_url("BMW E46") is None


# ── describe / suggest ───────────────────────────────────────────────────────


def test_describe_car_filters():
    parts = describe_search_url(VW_URL, CATEGORY_LABELS)
    assert parts == [
        "volkswagen klima",
        "Auto",
        "EZ: ab 2008",
        "Kraftstoff: cng, lpg",
        "km: ab 2",
        "Marke: volkswagen",
        "Getriebe: automatik",
        "Typ: kombi, suv",
    ]


def test_describe_location_radius_and_price():
    url = "https://www.kleinanzeigen.de/s-autos/berlin/preis:1000:5000/bmw/k0c216l3331r20"
    parts = describe_search_url(url, CATEGORY_LABELS)
    assert "Auto" in parts
    assert "+20km" in parts
    assert "1000-5000€" in parts


def test_describe_unknown_category_uses_slug():
    url = "https://www.kleinanzeigen.de/s-musikinstrumente/k0c74"
    assert describe_search_url(url, CATEGORY_LABELS) == ["musikinstrumente"]


def test_suggest_name():
    assert suggest_watch_name(VW_URL, CATEGORY_LABELS) == "Volkswagen Klima"
    assert suggest_watch_name("https://www.kleinanzeigen.de/s-autos/c216", CATEGORY_LABELS) == "Auto"
    assert (
        suggest_watch_name(
            "https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=bmw+e46", CATEGORY_LABELS
        )
        == "bmw e46"
    )


# ── parser / pagination ──────────────────────────────────────────────────────


def test_parser_keeps_all_attributes_verbatim():
    attrs = parse_kleinanzeigen_url(VW_URL)["attributes"]
    assert attrs["autos.km_i"] == "2,"
    assert attrs["autos.typ_s"] == "(kombi,suv)"


def test_parser_category_with_location_and_radius():
    parsed = parse_kleinanzeigen_url("https://www.kleinanzeigen.de/s-autos/berlin/k0c216l3331r20")
    assert parsed["category_id"] == 216
    assert parsed["radius"] == 20


def test_inject_page_search_form_url_uses_query_param():
    url = "https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=bmw&pageNum=1"
    assert inject_page(url, 1) == "https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=bmw"
    assert (
        inject_page(url, 3)
        == "https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=bmw&pageNum=3"
    )


# ── Telegram flow ────────────────────────────────────────────────────────────


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)


class FakeService:
    def __init__(self, watches=()):
        self.watches = list(watches)

    def list_watches(self):
        return list(self.watches)

    async def add_watch(self, watch):
        self.watches.append(watch)


def _run(handler, *texts):
    async def go():
        for i, text in enumerate(texts):
            await handler._handle_update(
                {"update_id": i, "message": {"chat": {"id": 1}, "text": text}}
            )

    asyncio.run(go())


def _handler(service=None):
    telegram = FakeTelegram()
    return TelegramCommandHandler(service or FakeService(), telegram, "1"), telegram


def test_pasted_url_creates_url_watch_with_suggested_name():
    service = FakeService([WatchConfig(name="Volkswagen Klima")])
    handler, telegram = _handler(service)

    _run(handler, f"Guck mal {VW_URL}", "-", "30")

    watch = service.watches[-1]
    assert watch.url == VW_URL
    assert watch.name == "Volkswagen Klima 2"  # suggestion made unique
    assert watch.interval_minutes == 30
    assert watch.query is None and watch.category_id is None
    assert "Getriebe: automatik" in telegram.sent[0]
    assert telegram.sent[-1].startswith("✅ Added watch")


def test_addurl_command_with_and_without_argument():
    handler, telegram = _handler()
    _run(handler, "/addurl not-a-url")
    assert "doesn't look like" in telegram.sent[-1]
    assert handler._wizard is None

    service = FakeService()
    handler, telegram = _handler(service)
    _run(handler, "/addurl", "nope", VW_URL, "VW", "-")
    assert "doesn't look like" in telegram.sent[1]
    assert service.watches[-1].name == "VW"
    assert service.watches[-1].interval_minutes == 15


def test_url_pasted_at_start_of_guided_add_switches_to_url_flow():
    service = FakeService()
    handler, _ = _handler(service)
    _run(handler, "/add", VW_URL, "VW", "-")
    assert service.watches[-1].url == VW_URL


def test_list_shows_url_watch_summary_and_link():
    service = FakeService([WatchConfig(name="A&B", url=VW_URL)])
    handler, telegram = _handler(service)
    _run(handler, "/list")
    line = telegram.sent[-1]
    assert "<b>A&amp;B</b>" in line
    assert '<a href="https://www.kleinanzeigen.de/s-autos/' in line
    assert "Marke: volkswagen" in line
