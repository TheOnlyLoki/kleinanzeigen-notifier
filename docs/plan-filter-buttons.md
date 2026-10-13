# Plan: Kategorie-Filter per Telegram-Buttons ("Weg 2")

Ziel: Suchen mit allen Filtern einer Kategorie (bei Autos z.B. Marke, Modell,
Erstzulassung, Kilometerstand, Kraftstoff, Getriebe, Fahrzeugtyp) direkt in
Telegram per Buttons zusammenklicken, ohne die Seite im Browser zu öffnen und
ohne pro Kategorie Filter von Hand im Code zu pflegen.

## Grundidee

**Die Buttons bauen nur eine Such-URL zusammen.** Seit Weg 1 kann ein Watch eine
kleinanzeigen.de-URL sein (`WatchConfig.url`, gescrapt mit `scrape_by_url`).
Weg 2 ist also nur eine neue Eingabemethode für diese URL. Scraping, Speicherung,
`/list` (`describe_search_url`) und Benachrichtigungen bleiben unverändert.

**Die Filter liest der Bot von der Seite selbst.** Die Ergebnisseite einer
Kategorie zeigt links ihre Filter. Jede Option (z.B. Marke "BMW (1.234)") ist
ein Link, dessen URL den Filter schon enthält (`…/k0c216+autos.marke_s:bmw`).
Der Bot lädt die Seite, liest diese Links aus und macht Buttons daraus. Klickt
man einen an, wird dessen URL zur neuen aktuellen Suche. Danach wird neu geladen
und neu ausgelesen. Daraus ergibt sich von selbst:

- **alle Kategorien** ohne Code pro Kategorie,
- **abhängige Filter**: "Modell" erscheint erst, wenn eine Marke gewählt ist,
  genau wie auf der Seite,
- **Trefferzahlen** pro Option und für die gesamte Suche (über den Breadcrumb,
  `_parse_breadcrumb` gibt es schon).

## So sieht es in Telegram aus

```
/filter
┌──────────────────────────────────────────┐
│ Kategorie wählen:                        │
│ [Auto] [Motorrad] [Wohnwagen] [Fahrrad]  │
│ [Andere: ID oder URL eingeben]           │
└──────────────────────────────────────────┘

→ Auto
┌──────────────────────────────────────────┐
│ 🚗 Auto · 312.480 Treffer                │
│ [Marke ▸] [Kraftstoff ▸] [Getriebe ▸]    │
│ [Fahrzeugtyp ▸] [Erstzulassung ▸]        │
│ [Kilometer ▸] [Preis ▸] [Ort/Umkreis ▸]  │
│ [✅ Fertig]  [✖ Abbrechen]               │
└──────────────────────────────────────────┘

→ Marke ▸
┌──────────────────────────────────────────┐
│ Marke (Seite 1/5)                        │
│ [Volkswagen 41.203] [BMW 30.118]         │
│ [Mercedes 28.874]   [Audi 22.410]  …     │
│ [◀] [▶] [↩ zurück]                       │
└──────────────────────────────────────────┘

→ BMW → zurück
┌──────────────────────────────────────────┐
│ 🚗 Auto · Marke: bmw · 30.118 Treffer    │
│ [Modell ▸]  ← neu, erst jetzt verfügbar  │
│ [Kraftstoff ▸] [Getriebe ▸] …            │
│ [✅ Fertig]  [✖ Abbrechen]               │
└──────────────────────────────────────────┘

→ Fertig → Name? (- für "Bmw") → Intervall? → ✅ wie bei /addurl
```

Es bleibt immer **eine einzige Nachricht**, die per `editMessageText` aktualisiert
wird. Der Chat wird also nicht zugespammt.

## Komponenten

### 1. Facetten-Extraktor (`notifier/facets.py`)

`async def load_facets(browser_manager, url) -> FacetPage`

- Seite mit dem vorhandenen `OptimizedPlaywrightManager` laden (Blocking von
  Ressourcen wie im Scraper) und **ein** `page.evaluate()` ausführen, das alles
  auf einmal zurückgibt, wie schon `_EXTRACT_ADS_JS`.
- **Möglichst unabhängig von CSS-Klassen**, ähnlich wie die Preiserkennung:
  - Alle Links nehmen, deren Filtersegment (`k0c216+…`) das der aktuellen URL
    um **genau ein Attribut** erweitert. Der Unterschied ist der Filter, z.B.
    `autos.marke_s:bmw`. Das ist unabhängig vom Layout.
  - Nach Attribut-Schlüssel gruppieren (`autos.marke_s` → Gruppe). Als Titel der
    Gruppe dient die nächstgelegene Überschrift im DOM ("Marke"), sonst
    `_ATTR_LABELS` aus `search_url.py`, sonst der rohe Schlüssel.
  - Label und Trefferzahl aus dem Linktext ("BMW (30.118)").
  - Ebenso Kategorie-Links (`c216` → `c223`) als "Unterkategorie"-Gruppe.
- Bereichsfilter (`*_i`: Erstzulassung, km, PS, Preis) sind auf der Seite
  Eingabefelder, keine Links. Erkennung über das Formular der Seitenleiste:
  `name`-Attribute der min/max-Felder. Was das genau ist, klärt Phase 0.
- Rückgabe: `FacetPage(url, total_results, groups=[FacetGroup(key, title,
  kind="options"|"range", options=[FacetOption(label, count, url)])])`.
  `url` pro Option ist die fertige Ziel-URL, damit muss der Bot keine URLs
  selbst zusammensetzen.
- Cache: `{url: FacetPage}` als LRU (z.B. 50 Einträge, 6 h). "Zurück" und das
  Blättern laden so nichts neu.

### 2. Telegram-Client (`notifier/telegram.py`)

Zusätzlich gebraucht:
- `send_message(..., reply_markup=None) -> message_id`
- `edit_message_text(chat_id, message_id, text, reply_markup)`
- `answer_callback_query(callback_query_id, text=None)`: ohne das zeigt
  Telegram am Button einen Lade-Spinner.

Klicks kommen als `callback_query` über das bestehende `getUpdates`.
`_handle_update` bekommt dafür einen zweiten Zweig, mit derselben
Chat-ID-Prüfung wie bisher.

### 3. Filter-Sitzung (`notifier/filter_session.py`)

Zustandsautomat, rein im Speicher, eine Sitzung (es gibt nur einen Admin-Chat):

```
FilterSession
  message_id        # die eine Nachricht, die editiert wird
  history: [url]    # für "zurück" / rückgängig
  current: FacetPage
  view: "overview" | ("group", key, page) | ("range", key)
  expires_at        # 15 min Inaktivität → verwerfen
```

- **`callback_data` darf höchstens 64 Bytes haben.** Deshalb enthält sie keine
  URLs, sondern nur kurze Indizes in die aktuelle Ansicht: `o:3` (Gruppe 3
  öffnen), `s:3:12` (Option 12 wählen), `p:3:2` (Seite 2), `b` (zurück),
  `d` (fertig), `x` (abbrechen). Dazu ein Sitzungs-Token, damit veraltete
  Buttons alter Nachrichten ignoriert werden: `<tok>|s:3:12`.
- Mehrfachauswahl (z.B. Kraftstoff "cng" **und** "lpg"): Kleinanzeigen kodiert
  das als `fuel_s:(cng,lpg)`. Die Seite selbst verlinkt meist nur Einzelwerte,
  also baut der Bot die Liste: gewählte Werte bekommen ✅, ein weiterer Klick
  hängt den Wert an bzw. entfernt ihn. Dafür braucht es einen kleinen
  URL-Umschreiber auf Basis von `parse_kleinanzeigen_url(...)["attributes"]`.
- Bereich (`*_i`): Textantwort im Format `2008-`, `-150000` oder `2008-2015`,
  daraus wird `ez_i:2008,` usw. Das Format ist durch die Beispiel-URLs bekannt.
- Ort/Umkreis: Textantwort (PLZ/Ort) und dann Buttons für den Radius. Die
  kanonische URL mit Orts-ID (`…c216l3331r20`) kommt aus der Weiterleitung von
  `/s-suchanfrage.html?locationStr=…&radius=…&categoryId=…` (`page.url` nach
  dem Laden).
- "Fertig" übergibt `current.url` an den bestehenden `/addurl`-Ablauf
  (`_start_url_wizard`): Name mit Vorschlag, dann Intervall. Ab da ist alles
  schon vorhanden.

### 4. Einstiegspunkte

- `/filter`: startet bei der Kategorieauswahl.
- `/add`: erste Frage "Buttons oder geführt (Stichwort/Preis)?". Optional, siehe
  offene Fragen.
- `/edit <nr|name>` für URL-Watches: startet die Sitzung mit `watch.url`, und
  "Fertig" ersetzt die URL des Watches. Damit lässt sich jede Suche später
  anpassen, egal ob sie per Button, per URL oder in der config angelegt wurde.
- Fallback überall: Zeigt eine Seite keine Filter (Layout geändert), schreibt
  der Bot "Filter konnten nicht gelesen werden, schick mir die URL" und
  verweist auf Weg 1. Die Funktion verschlechtert sich also, fällt aber nie
  ganz aus.

## Performance auf dem Pi

Jeder Klick, der einen Filter **setzt**, bedeutet einen Seitenaufruf
(≈1–3 s auf dem Pi nach den Optimierungen). Dagegen hilft:
- Blättern, Zurück und Gruppen öffnen kommen aus dem Cache, ohne Seitenaufruf.
- Sofort `answerCallbackQuery("⏳")`, damit der Nutzer eine Reaktion sieht.
- Facetten-Ladevorgänge teilen sich das Scrape-Limit
  (`NOTIFIER_MAX_CONCURRENT_SCRAPES`), haben aber Vorrang, weil ein Mensch
  wartet: eigene Prioritäts-Warteschlange vor dem Semaphor.
- Falls die Seite auch ohne Browser ausgeliefert wird (siehe curl-Test aus der
  Performance-Runde): Facetten mit `httpx` + `selectolax` laden, etwa 10× so
  schnell. Der Extraktor sollte deshalb von Anfang an auf einem HTML-String
  arbeiten können und nicht nur im Browser.

## Phasen

| # | Inhalt | Ergebnis | Aufwand |
|---|---|---|---|
| 0 | **Erkundung:** echtes HTML speichern von `/s-autos/c216`, `/s-autos/bmw/k0c216+autos.marke_s:bmw`, `/s-motorraeder-roller/c305` und `/s-wohnwagen-mobile/c220`. Prüfen: Wie sehen die Filter-Links aus, wie die Bereichsfelder, kommt die Seite auch per curl? | Fixtures in `tests/fixtures/`, Klarheit über das DOM | klein, **braucht dich** (kleinanzeigen.de ist von meiner Umgebung aus gesperrt) |
| 1 | `facets.py` + Unit-Tests gegen die Fixtures | `load_facets()` liefert Gruppen und Optionen für alle 4 Kategorien | mittel |
| 2 | Telegram-Client: Inline-Keyboards, Callbacks, Editieren | Buttons funktionieren | klein |
| 3 | `FilterSession`: Übersicht, Gruppe, Blättern, Zurück, Fertig → `_start_url_wizard` | `/filter` mit Einzelauswahl nutzbar | mittel |
| 4 | Mehrfachauswahl, Bereiche (`*_i`), Ort/Umkreis | Funktionsumfang wie auf der Seite | mittel |
| 5 | `/edit`, Einstieg über `/add`, Cache, Priorität vor dem Scrape-Semaphor, README | fertig | klein |

Jede Phase ist für sich auslieferbar. Nach Phase 3 ist die Funktion schon
brauchbar, Phase 4 macht sie vollständig.

**Für Phase 0** brauche ich von dir die vier Seiten als HTML, aufgenommen auf
dem Pi:

```sh
for u in s-autos/c216 "s-autos/bmw/k0c216+autos.marke_s:bmw" s-motorraeder-roller/c305 s-wohnwagen-mobile/c220; do
  curl -sL -A "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36" \
    "https://www.kleinanzeigen.de/$u" -o "$(echo "$u" | tr '/:+' '___').html"
done
ls -la *.html   # Dateien deutlich > 100 KB = echte Seite, sonst Bot-Sperre
```

Falls curl nur eine Sperrseite bekommt, schreibe ich in Phase 0 ein kleines
`tools/dump_page.py`, das die Seiten über den Chromium-Container des Notifiers
speichert.

## Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Kleinanzeigen ändert das Layout der Seitenleiste | Erkennung über URL-Unterschiede statt CSS-Klassen; Fixture-Tests; Rückfall auf "URL schicken" (Weg 1) |
| Mehr Seitenaufrufe → Bot-Erkennung | Cache; Aufrufe nur bei echtem Filterwechsel; geringe Menge (ein Mensch klickt) |
| `callback_data` max. 64 Bytes, Tastatur max. ~100 Buttons, Nachricht max. 4096 Zeichen | Indizes statt URLs; 8–10 Optionen pro Seite mit Blättern; Status kurz halten |
| Bereichsfilter nicht als Links erkennbar | Phase 0 klärt das Formular; notfalls bekannte `_i`-Schlüssel aus den gesammelten URLs |
| Langsame Klicks auf dem Pi | Cache, `⏳`-Rückmeldung, Vorrang vor geplanten Scrapes, optional httpx statt Browser |

## Offene Fragen an dich

1. Sollen die Buttons in `/add` der Standard werden, oder reicht ein eigenes
   `/filter`?
2. Welche Kategorien gehören in die Schnellauswahl? Bisher: Auto, Autoteile,
   Motorrad, Fahrrad, Wohnwagen, Boot.
3. Sprache der Bot-Texte: Die Befehle sind bisher englisch, die
   Benachrichtigungen deutsch. Für die neuen Menüs Deutsch?
