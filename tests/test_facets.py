"""
Unit tests for reading filter options from a search page (notifier/facets.py).

Run with:
  pytest tests/test_facets.py -v
"""

import asyncio
import os
from urllib.parse import unquote

import pytest

from notifier.facets import (
    CATEGORY_KEY,
    _COLLECT_JS,
    build_facet_page,
    current_price,
    parse_range,
    range_value,
    search_state,
    start_url,
    toggle_value,
    with_attribute,
    with_price,
)

BASE = "https://www.kleinanzeigen.de/s-autos/braunschweig/k0c216l2911r20"


def link(href, text, heading="", context=None):
    return {"href": href, "text": text, "heading": heading, "context": context or text}


SIDEBAR = {
    "links": [
        link("/s-autoteile-reifen/braunschweig/c223l2911r20", "Autoteile & Reifen (812)", "Kategorien"),
        link("/s-autos/braunschweig/bmw/k0c216l2911r20+autos.marke_s:bmw", "BMW", "Marke", "BMW (1.204)"),
        link("/s-autos/braunschweig/vw/k0c216l2911r20+autos.marke_s:volkswagen", "Volkswagen (2.311)", "Marke"),
        link("/s-autos/braunschweig/k0c216l2911r20+autos.fuel_s:diesel", "Diesel (3.100)", "Kraftstoffart"),
        link("/s-autos/braunschweig/k0c216l2911r20+autos.fuel_s:lpg", "Autogas (LPG) (95)", "Kraftstoffart"),
        link("/s-autos/braunschweig/k0c216l2911r20+autos.km_i:%2C50000", "bis 50.000 km (900)", "Kilometerstand"),
        # not filter options:
        link("/s-autos/braunschweig/seite:2/k0c216l2911r20", "2", "Seite"),  # pagination
        link("/s-autos/berlin/k0c216l3331r20", "Berlin", "Orte"),  # other location
        link("/s-autos/braunschweig/k0c216l2911r20+autos.marke_s:bmw+autos.fuel_s:lpg", "x", ""),  # 2 at once
        link("/s-anzeige/golf/123-216-1", "Golf", ""),  # a listing
        link("https://www.example.com/s-autos/c216", "Elsewhere", ""),  # other host
        link("/s-autos/braunschweig/k0c216l2911r20+autos.marke_s:bmw", "", "Marke"),  # no label
    ],
    "inputs": ["keywords", "autos.ez_i_from", "autos.ez_i_to", "minPrice"],
    "summary": "Autos 1 - 25 von 4.321 Gebrauchtwagen",
}


# ── URL state & editing ──────────────────────────────────────────────────────


def test_search_state():
    state = search_state(BASE + "+autos.fuel_s:(cng%2Clpg)+autos.marke_s:bmw")
    assert (state.category_id, state.location_id, state.radius) == (216, "2911", "20")
    assert state.attrs == {"autos.fuel_s": "(cng,lpg)", "autos.marke_s": "bmw"}

    form = search_state(start_url(216, "subaru", "38100", 20))
    assert form.category_id == 216 and not form.canonical


def test_search_state_ignores_keyword_that_looks_like_a_category():
    state = search_state("https://www.kleinanzeigen.de/s-autos/c3/k0c216+autos.marke_s:citroen")
    assert state.category_id == 216


def test_with_attribute_sets_sorts_and_removes():
    url = with_attribute(BASE, "autos.marke_s", "bmw")
    url = with_attribute(url, "autos.fuel_s", "(cng,lpg)")
    assert url.endswith("/k0c216l2911r20+autos.fuel_s:(cng%2Clpg)+autos.marke_s:bmw")
    assert with_attribute(url, "autos.fuel_s", None).endswith("/k0c216l2911r20+autos.marke_s:bmw")
    assert with_attribute(start_url(216), "autos.marke_s", "bmw") is None


def test_with_price_path_and_query():
    url = with_price(BASE, 500, 3500)
    assert url == "https://www.kleinanzeigen.de/s-autos/preis:500:3500/braunschweig/k0c216l2911r20"
    assert current_price(url) == (500, 3500)
    assert current_price(with_price(url, None, 2000)) == (None, 2000)
    assert "preis:" not in with_price(url, None, None)

    form = with_price(start_url(216, "subaru"), 1000, None)
    assert "minPrice=1000" in form and "maxPrice" not in form
    assert current_price(form) == (1000, None)


def test_toggle_value():
    assert toggle_value(None, "lpg") == "lpg"
    assert toggle_value("lpg", "cng") == "(lpg,cng)"
    assert toggle_value("(lpg,cng)", "lpg") == "cng"
    assert toggle_value("cng", "cng") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2008-2015", (2008, 2015)),
        ("2008-", (2008, None)),
        ("-150.000", (None, 150000)),
        ("2015 – 2008", (2008, 2015)),
        ("2008", (2008, None)),
        ("abc", None),
        ("-", None),
    ],
)
def test_parse_range(text, expected):
    assert parse_range(text) == expected


def test_range_value():
    assert range_value(2008, None) == "2008,"
    assert range_value(None, 150000) == ",150000"


def test_start_url():
    url = start_url(216, "subaru impreza", "Braunschweig", 20)
    assert url == (
        "https://www.kleinanzeigen.de/s-suchanfrage.html"
        "?keywords=subaru+impreza&categoryId=216&locationStr=Braunschweig&radius=20"
    )
    assert "radius" not in start_url(216, radius=20)  # radius only with a location


# ── build_facet_page ─────────────────────────────────────────────────────────


def test_groups_from_links_that_add_one_filter():
    page = build_facet_page(BASE, SIDEBAR)
    assert [g.key for g in page.groups] == [
        CATEGORY_KEY,
        "autos.marke_s",
        "autos.fuel_s",
        "autos.km_i",
        "autos.ez_i",
    ]
    assert page.total_results == 4321

    marke = page.group("autos.marke_s")
    assert marke.title == "Marke"
    assert [(o.label, o.value, o.count) for o in marke.options] == [
        ("BMW", "bmw", 1204),  # count from the list item around the link
        ("Volkswagen", "volkswagen", 2311),
    ]
    assert marke.options[0].url.startswith("https://www.kleinanzeigen.de/s-autos/")

    fuel = page.group("autos.fuel_s")
    assert [o.label for o in fuel.options] == ["Diesel", "Autogas (LPG)"]

    assert page.group("autos.km_i").is_range
    assert page.group("autos.km_i").options[0].value == ",50000"
    ez = page.group("autos.ez_i")  # learned from input names only
    assert ez.is_range and ez.options == [] and ez.title == "EZ"

    category = page.group(CATEGORY_KEY)
    assert [(o.label, o.value) for o in category.options] == [("Autoteile & Reifen", "223")]


def test_selected_values_stay_as_options():
    url = BASE + "+autos.fuel_s:diesel"
    raw = {
        "links": [
            link("/s-autos/braunschweig/k0c216l2911r20+autos.fuel_s:lpg", "LPG (95)", "Kraftstoffart"),
            link("/s-autos/braunschweig/k0c216l2911r20", "Filter entfernen", ""),  # removes -> ignored
        ]
    }
    fuel = build_facet_page(url, raw).group("autos.fuel_s")
    assert [o.value for o in fuel.options] == ["diesel", "lpg"]


def test_applied_filter_without_links_is_kept():
    page = build_facet_page(BASE + "+autos.marke_s:bmw", {"links": []})
    marke = page.group("autos.marke_s")
    assert marke and [o.value for o in marke.options] == ["bmw"]


def test_search_form_url_accepts_any_location():
    # Before the site's redirect the URL carries no location id yet
    page = build_facet_page(start_url(216, location="Braunschweig"), SIDEBAR)
    assert page.group("autos.marke_s")


def test_long_heading_falls_back_to_attribute_label():
    raw = {"links": [link(BASE + "+autos.shift_s:automatik", "Automatik", "x" * 40)]}
    assert build_facet_page(BASE, raw).group("autos.shift_s").title == "Getriebe"


# ── _COLLECT_JS in a real browser ────────────────────────────────────────────

FIXTURE_HTML = """<html><head><meta charset="utf-8"></head><body>
<div class="breadcrump-summary">Autos 1 - 25 von 4.321 Gebrauchtwagen</div>
<aside>
  <h2>Marke</h2>
  <ul>
    <li><a href="/s-autos/bmw/k0c216+autos.marke_s:bmw">BMW</a> <span>(1.204)</span></li>
    <li><a href="/s-autos/vw/k0c216+autos.marke_s:volkswagen">Volkswagen</a> <span>(2.311)</span></li>
  </ul>
  <section><h3>Kraftstoffart</h3>
    <a href="/s-autos/k0c216+autos.fuel_s:diesel">Diesel (3.100)</a>
  </section>
  <form><input name="autos.ez_i_from"><input name="autos.ez_i_to"></form>
</aside>
<main>
  <h2>Ergebnisse</h2>
  <article data-adid="1"><a href="/s-anzeige/golf/1">Golf</a>
    <a href="/s-autos/k0c216+autos.marke_s:volkswagen">VW</a></article>
</main>
</body></html>"""

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


@pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="needs the bundled Chromium")
def test_collect_js_in_browser():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROMIUM)
            page = await browser.new_page()
            await page.set_content(FIXTURE_HTML)
            raw = await page.evaluate(_COLLECT_JS)
            await browser.close()
            return raw

    raw = asyncio.run(run())
    assert [(l["text"], l["heading"]) for l in raw["links"]] == [
        ("BMW", "Marke"),
        ("Volkswagen", "Marke"),
        ("Diesel (3.100)", "Kraftstoffart"),
    ]  # links inside result cards are skipped
    assert raw["links"][0]["context"] == "BMW (1.204)"
    assert raw["inputs"] == ["autos.ez_i_from", "autos.ez_i_to"]

    page = build_facet_page("https://www.kleinanzeigen.de/s-autos/c216", raw)
    assert [g.key for g in page.groups] == ["autos.marke_s", "autos.fuel_s", "autos.ez_i"]
    assert [o.count for o in page.group("autos.marke_s").options] == [1204, 2311]
    assert page.total_results == 4321
    assert unquote(page.group("autos.fuel_s").options[0].url).endswith("+autos.fuel_s:diesel")
