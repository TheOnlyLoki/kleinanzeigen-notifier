# Ebay Kleinanzeigen API

> **This fork** turns the API below into a self-hosted **Telegram notifier**:
> configure one or more saved searches, get pinged when a matching listing
> appears or drops in price, and run the whole thing on a Raspberry Pi via
> Docker Compose with CI/CD already wired up. See [Telegram Notifier](#telegram-notifier)
> below for setup; everything else on this page describes the underlying
> scraping API it's built on.

<div align="center">
  <h3 align="center">Ebay Kleinanzeigen API</h3>

  <p align="center">
    A powerful API interface for Ebay-Kleinanzeigen.de that enables you to fetch listings and specific data.
  </p>

  <p align="center">
    <b>🚀 Looking for a ready-to-use solution?</b>
    <br />
    Try it at <a href="https://kleinanzeigen-agent.de/features/developer-api"><strong>kleinanzeigen-agent.de »</strong></a>
    <br />
    ✓ Automated Search Agents
    <br />
    ✓ Search & Detail API
    <br />
    <a href="https://github.com/DanielWTE/ebay-kleinanzeigen-api/issues">Report Bug</a>
    ·
    <a href="https://github.com/DanielWTE/ebay-kleinanzeigen-api/issues">Request Feature</a>
  </p>
</div>

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Getting Started

### Want to skip the setup?

Visit [kleinanzeigen-agent.de](https://kleinanzeigen-agent.de/features/developer-api) for our hosted solution with additional features and zero configuration required.

### Prerequisites

- Python 3.12 or higher
- pip (Python package manager)
- Playwright

### Installation

1. Clone the repository

```sh
git clone https://github.com/DanielWTE/ebay-kleinanzeigen-api.git
cd ebay-kleinanzeigen-api
```

2. Install dependencies

```sh
pip install -r requirements.txt
playwright install chromium
```

3. Start the API

```sh
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

#### Non-headless mode (visible browser)

Set the `HEADLESS` environment variable to `false` to watch the browser while scraping — useful during development:

```sh
HEADLESS=false uvicorn main:app --reload
```

### Installation using UV

#### Prerequisites

- uv package manager

#### Installation of dependencies

```sh
uv sync
```

#### Start the API

```sh
uv run uvicorn main:app --reload
```

#### Non-headless mode with UV

```sh
HEADLESS=false uv run uvicorn main:app --reload
```

### Installation using Docker

This fork's image is Alpine-based and connects to Chromium over the DevTools
protocol (CDP) rather than bundling a browser itself (Playwright doesn't
support Alpine/musl directly - see [Telegram Notifier](#telegram-notifier)
for why). Because of that it needs a companion browser container, so it's
distributed as a `docker-compose.yml` rather than a single `docker run`:

```sh
cp config.example.yaml config.yaml   # edit with your search(es)
cp .env.example .env                 # add your Telegram bot token
docker compose up -d
```

The API will be available at `http://localhost:8000`. If you only want the
scraping API (no notifier), leave `config.yaml` absent/empty - the notifier
loops simply won't start.

### API Endpoints

#### 1. Fetch Listings (Standard)

**Endpoint:** `GET /inserate`

**Description:** Retrieves a list of listings based on search criteria.

##### Query Parameters

- **`query`** *(string, optional)*: The search term (e.g., "fahrrad" to search for bicycles).
- **`location`** *(string, optional)*: The location or postal code to narrow the search (e.g., `10178` for Berlin).
- **`radius`** *(integer, optional)*: The search radius in kilometers from the specified location (e.g., `5` for a 5 km radius).
- **`min_price`** *(integer, optional)*: The minimum price in Euros for the listings (e.g., `200` for at least 200 Euros).
- **`max_price`** *(integer, optional)*: The maximum price in Euros for the listings (e.g., `500` for at most 500 Euros).
- **`page_count`** *(integer, optional)*: The number of pages to search or return (e.g., `5` for the first 5 pages, default is 1, max: 20 pages).
- **`min_publish_date`** *(datetime, optional)*: Stop fetching once a page contains listings published before this datetime. Listings older than the threshold are removed from the final results. Format: `YYYY-MM-DDTHH:MM:SS` (e.g., `2026-05-03T08:00:00`). Useful for intraday runs that should only collect new listings.

##### Example Requests

```http
GET /inserate?query=fahrrad&location=10178&radius=5&min_price=200&page_count=5
```

**With `min_publish_date` — stop when listings older than this morning are reached:**
```sh
curl "http://localhost:8000/inserate?query=fahrrad&page_count=10&min_publish_date=2026-05-04T08:00:00"
```

#### 2. Fetch Listing Details

**Endpoint:** `GET /inserat/{id}`

**Description:** Retrieves detailed information about a specific listing.

##### Path Parameters

- **`id`** *(string)*: The listing identifier. Two formats are accepted:

  | Format | Example | Source |
  |--------|---------|--------|
  | Full URL segment *(recommended)* | `3399373623-220-16792` | Last path segment of the listing `url` field from search results |
  | Plain adid | `3399373623` | The `adid` field from search results |

  Kleinanzeigen listing URLs follow the pattern:
  ```
  /s-anzeige/{slug}/{adid}-{category_id}-{location_id}
  ```
  The suffix `-{category_id}-{location_id}` is used by Kleinanzeigen for server-side routing (CDN/load balancer can route directly without a database lookup). Using the full segment avoids a potential server-side redirect — Kleinanzeigen may or may not redirect depending on the listing.

##### curl Examples

**With full URL segment (recommended, ~4s):**
```sh
curl -s http://localhost:8000/inserat/3399373623-220-16792 | python -m json.tool
```

**With plain adid only (also works, redirect may or may not occur):**
```sh
curl -s http://localhost:8000/inserat/3399373623 | python -m json.tool
```

**Tip:** When iterating over search results, use the `url` field directly — extract the last path segment to get the full id:
```sh
# Extract the id from a listing url
echo "https://www.kleinanzeigen.de/s-anzeige/fendt-bianco/3399373623-220-16792" | grep -oP '[^/]+$'
# → 3399373623-220-16792
```

##### Example Response

```json
{
  "success": true,
  "time_taken": 4.108,
  "data": {
    "id": "3399373623",
    "url_requested": "https://www.kleinanzeigen.de/s-anzeige/3399373623-220-16792",
    "url_redirected": "https://www.kleinanzeigen.de/s-anzeige/fendt-bianco-aktiv-550-kg/3399373623-220-16792",
    "categories": ["Wohnwagen & Reisemobile", "Wohnwagen"],
    "title": "Fendt Bianco Aktiv 550 KG",
    "status": "active",
    "price": {
      "amount": "12500",
      "currency": "€",
      "negotiable": true
    },
    "delivery": "pickup",
    "location": {
      "zip": "88299",
      "city": "Leutkirch im Allgäu"
    },
    "views": "142",
    "description": "Verkaufe unseren gepflegten Fendt Bianco...",
    "images": [
      "https://img.kleinanzeigen.de/api/v1/prod-ads/images/..."
    ],
    "details": {
      "Baujahr": "2015",
      "Zustand": "Gebraucht"
    },
    "features": {},
    "seller": {
      "name": "Max Mustermann",
      "type": "private"
    },
    "extra_info": {}
  },
  "performance_metrics": {
    "success_rate": 100,
    "time_taken": 4.108
  }
}
```

**Response fields:**

- **`data.id`** — Kleinanzeigen listing ID (numeric string).
- **`data.url_requested`** — The URL sent to Kleinanzeigen. When called with a plain adid, this is the short form without slug.
- **`data.url_redirected`** — The final URL after any server-side redirect. When called with a plain adid, this contains the full canonical URL including slug and routing suffix `{adid}-{category_id}-{location_id}`. Identical to `url_requested` when the full segment was passed.
- **`data.status`** — Listing status: `active`, `sold`, `reserved`, or `deleted`.
- **`data.price.negotiable`** — `true` if the price is marked as negotiable (VB).
- **`data.delivery`** — `pickup`, `shipping`, or `null`.
- **`data.details`** — Key/value pairs of listing attributes (e.g. Baujahr, Zustand).
- **`data.features`** — Additional configuration options if present.

#### 3. Fetch Listings with Details (Combined)

**Endpoint:** `GET /inserate-detailed`

**Description:** Retrieves listings and their detailed information in a single request.

##### Query Parameters

Same as `/inserate` endpoint, plus:

- **`max_concurrent_details`** *(integer, optional)*: Maximum concurrent detail fetches (default: 5, max: 10).

##### Example Request

```http
GET /inserate-detailed?query=laptop&page_count=2&max_concurrent_details=5
```

#### 4. Scrape by URL

**Endpoint:** `POST /inserate-by-url`

**Description:** Scrape listings using a full Kleinanzeigen search URL. All filters already encoded in the URL (category, brand, year, fuel type, transmission, car type, etc.) are preserved as-is. Page numbers are injected automatically for multi-page fetching. Use this when `/inserate` does not yet support a filter you need.

Pages are fetched **sequentially** to avoid IP-level rate limiting by Kleinanzeigen.

##### Request Body

```json
{
  "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
  "max_pages": 3
}
```

- **`url`** *(string, required)*: Any Kleinanzeigen search or category URL.
- **`max_pages`** *(integer, optional)*: Number of pages to fetch (default: 1). Each page returns up to 25 listings.
- **`min_publish_date`** *(datetime, optional)*: Stop fetching once a page contains listings published before this datetime. Listings older than the threshold are removed from the final results. Format: `YYYY-MM-DDTHH:MM:SS`. Supports intraday precision — e.g. `"2026-05-04T08:00:00"` keeps only listings from that morning onward.

##### curl Examples

**Single page — VW with Klima (25 results):**
```sh
curl -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 1
  }'
```

**3 pages — VW with Klima (up to 75 results):**
```sh
curl -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 3
  }'
```

**With full filters — VW Kombi/SUV, Automatik, CNG/LPG, ab 2008, 3 pages:**
```sh
curl -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.ez_i:2008%2C+autos.fuel_s:(cng%2Clpg)+autos.marke_s:volkswagen+autos.shift_s:automatik+autos.typ_s:(kombi%2Csuv)",
    "max_pages": 3
  }'
```

**Wohnwagen — Fendt, Klima, max 15.000 €, ab 2008:**
```sh
curl -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-wohnwagen-mobile/wohnwagen/preis::15000/klima/k0c220+wohnwagen_mobile.art_s:wohnwagen+wohnwagen_mobile.ez_i:2008%2C+wohnwagen_mobile.marke_s:fendt",
    "max_pages": 2
  }'
```

**With `min_publish_date` — stop once listings older than this morning are found (intraday run):**
```sh
curl -s -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 20,
    "min_publish_date": "2026-05-04T08:00:00"
  }' | python -m json.tool
```
Expected: `pages_requested` < 20, all `published_at` values ≥ `"2026-05-04T08:00:00"`.

**Verify early-stop: future cutoff → 0 results, only 1 page fetched:**
```sh
curl -s -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 5,
    "min_publish_date": "2099-01-01T00:00:00"
  }' | python -m json.tool
```
Expected: `"unique_results": 0`, `"pages_requested": 1`.

**Verify no false filtering: past cutoff → full 25 results:**
```sh
curl -s -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 1,
    "min_publish_date": "2000-01-01T00:00:00"
  }' | python -m json.tool
```
Expected: `"unique_results": 25`.

**Verify schema enforcement: plain date string → HTTP 422:**
```sh
curl -s -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen",
    "max_pages": 1,
    "min_publish_date": "2026-05-04"
  }' | python -m json.tool
```
Expected: HTTP 422 — the time component is required.

**Pretty-print the response with Python:**
```sh
curl -s -X POST http://localhost:8000/inserate-by-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen", "max_pages": 1}' \
  | python -m json.tool
```

##### Example Response

```json
{
  "success": true,
  "results": [
    {
      "adid": "3398033022",
      "url": "https://www.kleinanzeigen.de/s-anzeige/...",
      "title": "VW Golf Variant 2.0 TDI Automatik Klima",
      "price": "12500",
      "description": "Gepflegter Zustand, Erstbesitz...",
      "published_at": "2026-05-03T22:06:00"
    }
  ],
  "unique_results": 75,
  "total_results": 120140,
  "time_taken": 12.358,
  "performance_metrics": {
    "pages_requested": 3,
    "pages_successful": 3,
    "success_rate": 100.0,
    "average_page_time": 4.119
  },
  "browser_metrics": {
    "contexts_created": 3,
    "contexts_reused": 0,
    "contexts_in_pool": 3,
    "contexts_in_use": 0
  }
}
```

**Response fields:**

- **`results`**: Array of listing objects. Each listing contains:
  - `adid` — Kleinanzeigen internal listing ID
  - `url` — Direct link to the listing
  - `title` — Listing title
  - `price` — Price as string (numeric digits only, no currency symbol; empty string if not set)
  - `description` — Short teaser text from the listing card
  - `published_at` — ISO 8601 publish datetime (e.g. `"2026-05-03T22:06:00"`). Older listings show only the date at midnight (`"2026-04-26T00:00:00"`). `null` if not shown on the card.
- **`unique_results`**: Total number of listings returned across all fetched pages.
- **`total_results`**: Total hits reported by Kleinanzeigen for this search (e.g. `120140`). Extracted from the page breadcrumb on the first page load — no extra request needed.
- **`time_taken`**: Total elapsed time in seconds.
- **`performance_metrics.pages_requested`** / **`pages_successful`** / **`success_rate`**: Page-level fetch statistics.

#### 5. Convert URL to API Parameters

**Endpoint:** `POST /convert-url`

**Description:** Parses a Kleinanzeigen URL and returns two groups of parameters: `inserate_params` (what the `/inserate` endpoint currently understands) and `unmapped` (filters not yet supported by `/inserate`, such as category, brand, year, or transmission). Useful for inspecting which parts of a URL can be expressed via the structured API.

##### Request Body

```json
{
  "url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen"
}
```

##### curl Examples

**Basic — category + keyword only:**
```sh
curl -X POST http://localhost:8000/convert-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.marke_s:volkswagen"}' \
  | python -m json.tool
```

**With year filter:**
```sh
curl -X POST http://localhost:8000/convert-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.ez_i:2008%2C+autos.marke_s:volkswagen"}' \
  | python -m json.tool
```

**With full filters — fuel, transmission, body type:**
```sh
curl -X POST http://localhost:8000/convert-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.kleinanzeigen.de/s-autos/volkswagen/klima/k0c216+autos.ez_i:2008%2C+autos.fuel_s:(cng%2Clpg)+autos.marke_s:volkswagen+autos.shift_s:automatik+autos.typ_s:(kombi%2Csuv)"}' \
  | python -m json.tool
```

##### Example Response

```json
{
  "inserate_params": {
    "query": "klima",
    "page_count": 1
  },
  "unmapped": {
    "category_slug": "s-autos",
    "subcategory": "volkswagen",
    "category_id": 216,
    "year_from": 2008,
    "year_to": null,
    "brands": ["volkswagen"]
  }
}
```

### Documentation

#### API Response Format

##### `GET /inserate` and `GET /inserate-detailed`

```json
{
  "success": true,
  "time_taken": 1.23,
  "unique_results": 25,
  "data": [
    {
      "adid": "123456",
      "url": "https://www.kleinanzeigen.de/s-anzeige/...",
      "title": "Example Item",
      "price": "100",
      "description": "Short teaser text..."
    }
  ],
  "performance_metrics": {
    "pages_requested": 1,
    "pages_successful": 1,
    "success_rate": 100.0,
    "average_page_time": 1.23
  }
}
```

The `/inserate-detailed` response additionally nests a `details` object inside each listing with full description, seller info, and location.

##### `POST /inserate-by-url`

```json
{
  "success": true,
  "results": [
    {
      "adid": "123456",
      "url": "https://www.kleinanzeigen.de/s-anzeige/...",
      "title": "Example Item",
      "price": "100",
      "description": "Short teaser text...",
      "published_at": "2026-05-03T22:06:00"
    }
  ],
  "unique_results": 75,
  "total_results": 120140,
  "time_taken": 12.358,
  "performance_metrics": {
    "pages_requested": 3,
    "pages_successful": 3,
    "success_rate": 100.0,
    "average_page_time": 4.119
  },
  "browser_metrics": { ... }
}
```

`total_results` is the total hit count shown by Kleinanzeigen (e.g. `120140`). It is extracted from the breadcrumb of the first page — no extra request is made. The field is omitted if the breadcrumb is not found.

##### `POST /convert-url`

```json
{
  "inserate_params": {
    "query": "klima",
    "page_count": 1
  },
  "unmapped": {
    "category_slug": "s-autos",
    "subcategory": "volkswagen",
    "category_id": 216,
    "year_from": 2008,
    "year_to": null,
    "brands": ["volkswagen"]
  }
}
```

## Telegram Notifier

This fork adds a background notifier on top of the scraping API: define one
or more saved searches ("watches") in `config.yaml`, and it polls each on its
own interval, comparing results against what it's already seen, and sends a
Telegram message for:

- **`new_listing`** - a listing matching the watch's filters that wasn't seen before
- **`price_drop`** - a previously-seen listing whose price has gone down

More notification kinds can be added the same way (`notifier/watchers.py` is
where that comparison logic lives).

### Configuration

Two files, neither committed to git:

- **`config.yaml`** (copy from `config.example.yaml`) - your watches. No secrets.
- **`.env`** (copy from `.env.example`) - `TELEGRAM_BOT_TOKEN` and a default `TELEGRAM_CHAT_ID`.

```yaml
watches:
  - name: "BMW E46"
    query: "BMW E46"
    category_id: 216          # Auto - excludes parts/accessories. See notifier/commands.py CATEGORY_LABELS for others
    location: "12345"         # PLZ or place name
    radius: 30                # km - allowed steps: 5,10,20,30,50,100,150,200
    min_price: 500
    max_price: 3500
    page_count: 1
    interval_minutes: 15
    notify_on: [new_listing, price_drop]
    # chat_id: "123456789"  # optional per-watch override
```

Seen-ad state is kept per watch as a small JSON file under `data/` (mounted
as a volume in `docker-compose.yml`), so it survives restarts/redeploys.

### Getting a Telegram chat_id

1. Create a bot via [@BotFather](https://t.me/BotFather) and grab its token into `.env`.
2. Open a chat with your bot in Telegram and send it any message (e.g. `/start`).
3. Fetch `https://api.telegram.org/bot<TOKEN>/getUpdates` - the reply contains
   your `chat.id`. Put that in `.env` as `TELEGRAM_CHAT_ID` (or per-watch).

### Managing watches from Telegram

Once `TELEGRAM_CHAT_ID` is set, that chat can manage watches directly - no
manual `config.yaml` editing needed:

- **`/list`** - show all configured watches
- **`/add`** - guided, step-by-step: asks for name, keywords, category,
  location, radius, price range, and check interval, then starts watching
  immediately. Category accepts a friendly name (auto, motorrad, fahrrad, ...)
  or a raw kleinanzeigen.de category id
- **`/delete <number|name>`** - remove a watch (number from `/list`, or its
  exact name)
- **`/poll [number|name]`** - scrape right now instead of waiting for the
  interval; polls every watch if no argument is given
- **`/cancel`** - abort an in-progress `/add`
- **`/help`** - list commands

Changes are written straight back to `config.yaml` (hence that volume is
mounted read-write) and take effect immediately - no restart required.
Only the chat matching `TELEGRAM_CHAT_ID` is accepted; commands from any
other chat are logged and ignored, since the bot's username is publicly
findable on Telegram.

### Why Alpine works here despite Playwright not supporting it

### Why Alpine works here despite Playwright not supporting it

Playwright's Python package has no official Alpine/musl support: its browser
downloads and its bundled Node "driver" binary are glibc-only. This image
sidesteps both problems instead of fighting them:

- **No local browser.** `docker-compose.yml` runs Chromium in its own
  `zenika/alpine-chrome` sidecar container; the app connects to it remotely
  over the DevTools protocol (`BROWSER_CDP_URL`, already supported by
  `utils/browser.py`). The app image never launches or bundles a browser.
- **No bundled Node driver.** The `playwright` pip package is pure Python +
  JS with one glibc-linked `node` executable inside it. The Dockerfile
  installs Alpine's own musl-native `nodejs` via `apk` and points
  `PLAYWRIGHT_NODEJS_PATH` at it, so that embedded binary is simply never
  run. `greenlet` (Playwright's other native dependency) does publish real
  musllinux wheels, so it installs normally.

This was validated end-to-end locally (multi-arch, including emulated
arm64) before landing: driver handshake, then an actual scrape against the
live site through the sidecar container. Net effect: a ~390MB app image
instead of ~2.4GB, and code changes only rebuild/push that small image - the
browser sidecar is pulled once from Docker Hub and rarely changes.

### Deploying to a Raspberry Pi

```sh
git clone <your fork>
cd kleinanzeigen-notifier
cp config.example.yaml config.yaml   # edit
cp .env.example .env                 # add your token
docker compose up -d
```

CI (`.github/workflows/ci.yml`) builds and pushes a multi-arch
(`linux/amd64` + `linux/arm64`) image to GHCR on every push to `main`, so the
Pi pulls a native arm64 image rather than emulating amd64.

**One-time GHCR step:** packages pushed via `GITHUB_TOKEN` default to
*private*. Either make the package public (repo → Packages → package
settings → Change visibility), or `docker login ghcr.io` on the Pi with a
personal access token that has `read:packages`, so both `docker compose pull`
and Watchtower (below) can fetch it.

### Continuous deployment

`docker-compose.yml` includes a [Watchtower](https://containrrr.dev/watchtower/)
service that polls GHCR every 30 minutes and redeploys the `app` container
when a new image digest appears - no inbound access to the Pi required,
which is what makes it a better fit here than a webhook/SSH-triggered
redeploy. It's scoped to only the `app` service via
`com.centurylinklabs.watchtower.enable=true`, so it won't touch unrelated
containers on the Pi.

## Performance Features

## 📊 Performance Benchmarks

Performance tests conducted on **Arch Linux** with **Intel i7-1260P** processor:

| Endpoint | Operation | Avg Time | Min Time | Max Time | Success Rate | Results |
|----------|-----------|----------|----------|----------|--------------|---------|
| Root | API status check | 0.004s | 0.001s | 0.009s | 100% | - |
| Listings | 1 page search | 1.209s | 1.081s | 1.596s | 100% | 25 |
| Listings | 5 pages search | 2.436s | 2.328s | 2.532s | 100% | 125 |
| Listings | 10 pages search | 4.989s | 4.790s | 5.229s | 100% | 250 |
| Details | Single listing details | 1.072s | 0.928s | 1.276s | 100% | 1 |
| Combined | 1 page + details | 9.058s | 8.804s | 9.319s | 100% | 25 |
| Combined | 2 pages + details | 17.512s | 17.063s | 18.038s | 100% | 50 |

### Performance Highlights

- **Single page search**: ~1.2s average response time
- **10-page search**: ~5.0s average response time  
- **Individual listing details**: ~1.1s average response time
- **Combined search + details**: ~9.1s average for 1 page with full details

*All tests performed with uvloop optimization and browser context pooling enabled.*

### 🚀 Technical Features

- **uvloop Integration**: High-performance asyncio event loop
- **Context Pooling**: Efficient browser resource reuse
- **Memory Optimization**: Automatic garbage collection and efficient processing
- **Intelligent Concurrency**: Optimal concurrent processing with resource control

### 🔧 Production Ready

- **Clean API Responses**: Minimal overhead with essential data only
- **Resource Management**: Automatic cleanup and efficient resource usage
- **Scalable Architecture**: Handles concurrent requests efficiently
- **Docker Optimized**: Fast container builds with uv package manager

API documentation is available at `http://localhost:8000/docs` when running locally.

## License

Distributed under the MIT License. See `LICENSE` for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Changelog

### May 2026 — Multi-image fetching, deleted-ad detection, media response restructure ⚠️ Breaking

**Maintainer:** [Darko Palić](https://github.com/dpalic)

#### ⚠️ Breaking changes

##### 1. `GET /inserat/{id}` — response shape changed

The `images` and `views` top-level fields have been replaced. The data is still present
but lives in a different location.

| Field | Before | After |
|---|---|---|
| Image URLs | `data.images` → `["url"]` (one image) | `data.media.images.urls` → `["url1", "url2", …]` (all images) |
| Image count | not provided | `data.media.images.count` |
| Views | `data.views` | `data.extra_info.views` (unchanged location — was already there) |

**How to migrate:**

```python
# Before
image = response["data"]["images"]        # single URL string or list of one
views = response["data"]["views"]

# After
images = response["data"]["media"]["images"]["urls"]   # list of all gallery images
count  = response["data"]["media"]["images"]["count"]
views  = response["data"]["extra_info"]["views"]       # unchanged
```

```javascript
// Before
const image = data.images;
const views = data.views;

// After
const images = data.media.images.urls;   // array of all gallery images
const count  = data.media.images.count;
const views  = data.extra_info.views;    // unchanged
```

##### 2. `GET /inserat/{id}` — deleted/expired ads now return HTTP 404

Previously, fetching a deleted or expired ad returned HTTP 200 with `success: true`
and empty fields (title `"[ERROR] Title not found"`, status `"active"`).

Now, two conditions are detected and return HTTP **404** with `success: false`:
- The URL redirects away from the ad page (ad deleted — Kleinanzeigen redirects to homepage)
- The page contains `#srchrslt-adexpired` (ad expired page)

**How to migrate:**

```python
# Before — callers had to inspect fields to detect a missing ad
resp = requests.get(f"/inserat/{id}", params={"batch_id": "x"})
data = resp.json()["data"]
if data["title"] == "[ERROR] Title not found":
    # ad was gone

# After — standard HTTP semantics
resp = requests.get(f"/inserat/{id}", params={"batch_id": "x"})
if resp.status_code == 404:
    # ad is deleted or expired
    print(resp.json()["detail"]["status"])  # "deleted"
else:
    data = resp.json()["data"]
```

```javascript
// Before
const res = await fetch(`/inserat/${id}?batch_id=x`);
const { data } = await res.json();
if (data.title === "[ERROR] Title not found") { /* gone */ }

// After
const res = await fetch(`/inserat/${id}?batch_id=x`);
if (res.status === 404) {
    const { detail } = await res.json();
    // detail.status === "deleted"
} else {
    const { data } = await res.json();
}
```

#### New fields (non-breaking, additive)

- **`data.scraped_at`** — ISO 8601 UTC timestamp of when the ad was scraped
- **`data.url_requested`** — URL sent to Kleinanzeigen (built from the id)
- **`data.url_redirected`** — final URL after any server-side redirect
- **`data.seller.user_id`** — numeric Kleinanzeigen user ID extracted from the profile link href

#### Improvements (non-breaking)

- **All gallery images returned** — previously only the first image was fetched
  (`#viewad-image`). Now all images from `.galleryimage-element img` are returned,
  including `data-src` lazy-loaded images. Deduplication applied.
- **`POST /inserate-by-url` — smarter pagination** — breadcrumb is now fully parsed
  on page 1 to determine the exact page count. Fetching stops automatically when the
  real page count is reached, avoiding wasted requests for non-existent pages.
- **`POST /inserate-by-url` — URL pagination fix** — page injection now correctly
  handles complex URLs with filters before the category segment
  (e.g. `anzeige:angebote`, `preis::N`).
- **Healthcheck endpoint** — `GET /health` added for load balancer / uptime monitoring.

#### Tests

- 16 live integration tests in `tests/test_inserat_live.py` covering active ads
  (both full-segment and plain adid formats), all response fields, and deleted-ad
  detection (HTTP 404, `status: "deleted"`) for 2 known-expired ad IDs.

---

### May 2026 — `GET /inserat/{id}` URL tracking (`url_requested` / `url_redirected`)

**Maintainer:** [Darko Palić](https://github.com/dpalic)

#### Changed

- **`GET /inserat/{id}` response** now includes two URL fields in `data`:
  - `url_requested` — the URL that was sent to Kleinanzeigen (built from the id passed to the endpoint).
  - `url_redirected` — the final URL as reported by the browser after any server-side redirect. Identical to `url_requested` when no redirect occurred.
- Useful when calling with a plain adid (e.g. `/inserat/3399373623`) to discover the canonical URL including slug and routing suffix.

#### Tests

- 13 live integration tests in `tests/test_inserat_live.py` covering both id formats (full segment and plain adid), `url_requested`, `url_redirected`, content fields, and status validation.

---

### May 2026 — Intraday `min_publish_date` precision (datetime)

**Maintainer:** [Darko Palić](https://github.com/dpalic)

#### Changed

- **`min_publish_date` upgraded from `date` to `datetime`** on both `GET /inserate` and `POST /inserate-by-url`. The parameter now accepts a full ISO 8601 datetime string (`YYYY-MM-DDTHH:MM:SS`) instead of a plain date. This enables intraday filtering — e.g. `"2026-05-04T08:00:00"` keeps only listings published from that exact time onward, not just from that calendar day.
- The internal comparison in `_page_has_old_listings` and `_filter_by_min_publish_date` now operates on full `datetime` objects instead of stripping to `.date()`, preserving hour/minute precision.
- German date strings (`"Heute, 08:46"`, `"Gestern, 14:29"`) already produced full ISO 8601 datetimes — no parsing changes required.

#### Tests

- 3 new live integration tests in `tests/test_inserate_by_url_live.py` covering `min_publish_date`:
  - Future cutoff (`2099-01-01T00:00:00`) returns 0 results and stops after page 1.
  - Past cutoff (`2000-01-01T00:00:00`) does not filter anything — result count unchanged.
  - All returned `published_at` values satisfy the cutoff when a real intraday threshold is used.

---

### May 2026 — URL-passthrough scraping, publish date, rate-limit fixes

**Maintainer:** [Darko Palić](https://github.com/dpalic)

#### New endpoints

- **`POST /inserate-by-url`** — scrape listings using any full Kleinanzeigen search URL. All filters encoded in the URL (category, brand, year, fuel type, transmission, body type, etc.) are preserved as-is. Page numbers are injected automatically for multi-page fetching. Response includes `total_results` (total hit count from the page breadcrumb, no extra request) and `published_at` on every listing.
- **`POST /convert-url`** — parse a Kleinanzeigen URL into `inserate_params` (what `/inserate` understands today) and `unmapped` (filters not yet supported by the structured endpoint).

#### New listing field

- **`published_at`** — ISO 8601 publish datetime added to every listing result (e.g. `"2026-05-04T22:06:00"`). Extracted from the listing card during the existing page load — no extra request. Older listings return a date-only value (`"2026-04-26T00:00:00"`); `null` if not shown on the card.

#### Bug fixes

- **Cookie clearing on context release** — browser contexts now have session cookies cleared before returning to the pool, preventing Kleinanzeigen tracking state from carrying over between unrelated requests and causing silent empty results on pages 2+.
- **Inter-page delay** — a 2-second pause between sequential page fetches within a single multi-page scrape reduces bot-detection false positives.

#### Tests

- 11 unit tests for `/convert-url` (`tests/test_convert_url.py`, no server required)
- 14 live integration tests for `/inserate-by-url` (`tests/test_inserate_by_url_live.py`), covering JSON structure, `published_at`, `total_results`, and exact result counts for 1–4 pages (25 / 50 / 75 / 100 results)
