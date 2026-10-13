"""
Unit tests for /filter and /edit (notifier/filter_session.py) driven through
the Telegram command handler, against a simulated kleinanzeigen.de.

Run with:
  pytest tests/test_filter_session.py -v
"""

import asyncio
from urllib.parse import parse_qs, unquote, urlsplit

from notifier.commands import TelegramCommandHandler
from notifier.config import WatchConfig
from notifier.facets import build_facet_page, current_price, search_state, with_attribute

BRANDS = {"bmw": "BMW", "subaru": "Subaru", "volkswagen": "Volkswagen"}
MODELS = {"impreza": "Impreza", "forester": "Forester"}
FUELS = {"diesel": "Diesel", "benzin": "Benzin", "lpg": "Autogas (LPG)"}


def fake_site(url):
    """Mimics the site: the search form redirects to a canonical URL, and the
    sidebar links each apply one more filter. Marke disappears once chosen,
    Modell appears instead (a dependent filter)."""
    parts = urlsplit(url)
    if parts.path == "/s-suchanfrage.html":
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        segments = ["s-autos"]
        if query.get("locationStr"):
            segments.append(query["locationStr"].lower())
        if query.get("keywords"):
            segments.append(query["keywords"].replace(" ", "-"))
        head = f"k0c{query.get('categoryId', '216')}"
        if query.get("locationStr"):
            head += "l2911" + (f"r{query['radius']}" if query.get("radius") else "")
        url = "https://www.kleinanzeigen.de/" + "/".join(segments + [head])
        for key in ("minPrice", "maxPrice"):
            assert key not in query  # not used in these tests

    state = search_state(url)
    links = []

    def add(key, value, label, heading, count):
        href = urlsplit(with_attribute(url, key, value)).path
        links.append({"href": href, "text": label, "context": f"{label} ({count})", "heading": heading})

    if "autos.marke_s" not in state.attrs:
        for i, (value, label) in enumerate(BRANDS.items()):
            add("autos.marke_s", value, label, "Marke", 1000 + i)
    else:
        for value, label in MODELS.items():
            add("autos.modell_s", value, label, "Modell", 50)
    for value, label in FUELS.items():
        if state.attrs.get("autos.fuel_s") != value:
            add("autos.fuel_s", value, label, "Kraftstoffart", 300)

    total = 5000 // (1 + len(state.attrs))
    raw = {
        "links": links,
        "inputs": ["autos.ez_i_from", "autos.ez_i_to"],
        "summary": f"Autos 1 - 25 von {total} Gebrauchtwagen",
    }
    return build_facet_page(url, raw)


class FakeTelegram:
    def __init__(self):
        self.messages = {}  # id -> (text, keyboard)
        self.sent = []  # plain messages, in order
        self.answers = []
        self._next_id = 100

    async def send_message(self, chat_id, text, keyboard=None):
        self._next_id += 1
        self.messages[self._next_id] = (text, keyboard)
        self.sent.append(text)
        return self._next_id

    async def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.messages[message_id] = (text, keyboard)

    async def answer_callback(self, callback_id, text=None):
        self.answers.append(text)


class FakeService:
    def __init__(self, watches=()):
        self.watches = list(watches)
        self.loaded = []
        self.updated = []
        self.fail_loads = False

    def list_watches(self):
        return list(self.watches)

    async def add_watch(self, watch):
        self.watches.append(watch)

    async def update_watch_url(self, name, url):
        self.updated.append((name, url))
        return True

    async def load_facets(self, url):
        self.loaded.append(url)
        await asyncio.sleep(0)
        if self.fail_loads:
            raise RuntimeError("site down")
        return fake_site(url)


class Chat:
    def __init__(self, watches=()):
        self.service = FakeService(watches)
        self.telegram = FakeTelegram()
        self.handler = TelegramCommandHandler(self.service, self.telegram, "1")
        self.loop = asyncio.new_event_loop()
        self._update_id = 0

    def _run(self, update):
        async def go():
            await self.handler._handle_update(update)
            while self.handler._background:
                await asyncio.gather(*list(self.handler._background))

        self.loop.run_until_complete(go())

    def say(self, text):
        self._update_id += 1
        self._run({"update_id": self._update_id, "message": {"chat": {"id": 1}, "text": text}})

    def menu_text(self):
        return self.telegram.messages[self._menu_id()][0]

    def buttons(self):
        keyboard = self.telegram.messages[self._menu_id()][1] or []
        return [button for row in keyboard for button in row]

    def _menu_id(self):
        return max(i for i, (_, kb) in self.telegram.messages.items() if kb)

    def tap(self, label, data=None):
        if data is None:
            matches = [d for text, d in self.buttons() if label in text]
            assert matches, f"no button {label!r} in {[t for t, _ in self.buttons()]}"
            data = matches[0]
        self._update_id += 1
        self._run(
            {
                "update_id": self._update_id,
                "callback_query": {"id": str(self._update_id), "data": data, "message": {"chat": {"id": 1}}},
            }
        )

    @property
    def url(self):
        return self.handler._filter._page.url


def test_full_filter_flow_creates_url_watch():
    chat = Chat()
    chat.say("/filter")
    assert "Pick a category" in chat.menu_text()

    chat.tap("Auto")
    chat.say("subaru")
    chat.say("Braunschweig")
    chat.tap("20 km")
    assert chat.service.loaded[-1].endswith(
        "?keywords=subaru&categoryId=216&locationStr=Braunschweig&radius=20"
    )
    assert chat.url == "https://www.kleinanzeigen.de/s-autos/braunschweig/subaru/k0c216l2911r20"
    assert "5.000 results" in chat.menu_text()

    # Marke: pick one -> group disappears, Modell (dependent) appears
    chat.tap("Marke ▸")
    assert [t for t, _ in chat.buttons()][:3] == ["BMW (1.000)", "Subaru (1.001)", "Volkswagen (1.002)"]
    chat.tap("Subaru")
    assert search_state(chat.url).attrs["autos.marke_s"] == "subaru"
    labels = [t for t, _ in chat.buttons()]
    assert "✖ Marke: subaru" in labels and "Modell ▸" in labels

    # Kraftstoff: multi-select stays in the group, with ✅ on chosen values
    chat.tap("Kraftstoffart ▸")
    chat.tap("Diesel")
    assert "✅ diesel" in [t for t, _ in chat.buttons()]
    chat.tap("Autogas")
    assert search_state(chat.url).attrs["autos.fuel_s"] == "(diesel,lpg)"
    chat.tap("✅ Diesel")
    assert search_state(chat.url).attrs["autos.fuel_s"] == "lpg"
    chat.tap("Back")

    # Range filter learned from the page's inputs: typed in
    chat.tap("EZ ▸")
    assert "2008-2015" in chat.menu_text()
    chat.say("2008-")
    assert unquote(chat.url).endswith("+autos.ez_i:2008,+autos.fuel_s:lpg+autos.marke_s:subaru")
    assert "✖ EZ: ab 2008" in [t for t, _ in chat.buttons()]

    # Price, then undo it
    chat.tap("Preis ▸")
    chat.say("500-3500")
    assert current_price(chat.url) == (500, 3500)
    chat.tap("Undo")
    assert current_price(chat.url) == (None, None)

    # Remove a filter with ✖
    chat.tap("✖ EZ")
    assert "autos.ez_i" not in search_state(chat.url).attrs

    final_url = chat.url
    menu_id = chat._menu_id()
    chat.tap("Done")
    assert chat.telegram.messages[menu_id][0].startswith("✅ Search ready:")
    assert "Name for this watch?" in chat.telegram.sent[-1]
    chat.say("-")
    chat.say("30")

    watch = chat.service.watches[-1]
    assert watch.url == final_url
    assert watch.interval_minutes == 30
    assert chat.telegram.sent[-1].startswith("✅ Added watch")
    assert chat.handler._filter is None


def test_skipping_keywords_and_location():
    chat = Chat()
    chat.say("/filter")
    chat.say("motorrad")  # category by name instead of a button
    chat.tap("Skip")
    chat.tap("Skip")
    assert chat.service.loaded[-1].endswith("?categoryId=305")


def test_unknown_category_text_asks_again():
    chat = Chat()
    chat.say("/filter")
    chat.say("raumschiff")
    assert "Unknown category" in chat.menu_text()
    assert chat.service.loaded == []


def test_edit_keyword_watch_turns_it_into_url_watch():
    watch = WatchConfig(name="Subaru", query="subaru", category_id=216, location="Braunschweig", radius=20)
    chat = Chat([watch])
    chat.say("/edit subaru")
    assert chat.service.loaded[0].endswith(
        "?keywords=subaru&categoryId=216&locationStr=Braunschweig&radius=20"
    )
    chat.tap("Marke ▸")
    chat.tap("Subaru")
    chat.tap("Done")
    name, url = chat.service.updated[-1]
    assert name == "Subaru"
    assert url.endswith("/k0c216l2911r20+autos.marke_s:subaru")
    assert chat.telegram.sent[-1].startswith("✅ Updated 'Subaru'.")


def test_edit_needs_a_unique_match():
    chat = Chat([WatchConfig(name="Subaru A"), WatchConfig(name="Subaru B")])
    chat.say("/edit subaru")
    assert "matches several watches" in chat.telegram.sent[-1]
    assert chat.service.loaded == []


def test_stale_menu_and_cancel():
    chat = Chat()
    chat.say("/filter")
    old_token_data = chat.buttons()[0][1]
    chat.say("/cancel")
    assert chat.telegram.sent[-1] == "Cancelled."
    chat.tap("", data=old_token_data)
    assert "expired" in chat.telegram.answers[-1]


def test_tap_while_loading_is_rejected():
    chat = Chat()
    chat.say("/filter")
    chat.tap("Auto")
    chat.tap("Skip")
    session = chat.handler._filter

    async def tap_during_load():
        data = f"{session.token}|skip"
        async with session._lock:  # a load in progress
            await chat.handler._handle_update(
                {"callback_query": {"id": "x", "data": data, "message": {"chat": {"id": 1}}}}
            )

    chat.loop.run_until_complete(tap_during_load())
    assert chat.telegram.answers[-1] == "⏳ Still loading…"


def test_load_failure_offers_retry():
    chat = Chat()
    chat.service.fail_loads = True
    chat.say("/filter")
    chat.tap("Auto")
    chat.tap("Skip")
    chat.tap("Skip")
    assert "Couldn't load" in chat.menu_text()
    chat.service.fail_loads = False
    chat.tap("Retry")
    assert "results" in chat.menu_text()


def test_other_chats_buttons_are_ignored():
    chat = Chat()
    chat.say("/filter")
    data = chat.buttons()[0][1]
    chat._run({"update_id": 99, "callback_query": {"id": "9", "data": data, "message": {"chat": {"id": 666}}}})
    assert chat.telegram.answers == []
    assert chat.service.loaded == []


def test_plain_text_during_filter_goes_to_the_session_not_the_url_wizard():
    chat = Chat()
    chat.say("/filter")
    chat.tap("Auto")
    chat.say("https://www.kleinanzeigen.de/s-autos/k0c216")  # answers "keywords?"
    assert chat.handler._wizard is None
    assert "Location" in chat.menu_text()


def test_opening_another_filter_drops_a_pending_price_prompt():
    chat = Chat()
    chat.say("/filter")
    chat.tap("Auto")
    chat.tap("Skip")
    chat.tap("Skip")
    chat.tap("Preis ▸")
    chat.tap("Marke ▸")
    assert not chat.handler._filter.awaiting_text
    loads = len(chat.service.loaded)
    chat.say("500-1000")  # not an answer to anything now
    assert len(chat.service.loaded) == loads
