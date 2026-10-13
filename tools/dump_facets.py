"""Show which filters /filter finds on a real search page, and save its HTML.

Runs inside the app container, using the same browser as the notifier:

    docker compose exec app python -m tools.dump_facets "https://www.kleinanzeigen.de/s-autos/c216"
    docker compose exec app python -m tools.dump_facets "<url>" --html /app/data/autos.html

The saved HTML can be copied off the Pi (data/ is a mounted volume) and used
as a test fixture when the site's sidebar changes.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from notifier.facets import _COLLECT_JS, build_facet_page
from scrapers.inserate_ultra_optimized import _block_unneeded_resources
from utils.browser import OptimizedPlaywrightManager


async def main(url: str, html_path: Path | None) -> None:
    manager = OptimizedPlaywrightManager(max_contexts=1, max_concurrent=1)
    await manager.start()
    try:
        context = await manager.get_context()
        page = await context.new_page()
        await _block_unneeded_resources(page)
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        raw = await page.evaluate(_COLLECT_JS)
        if html_path:
            html_path.write_text(await page.content(), encoding="utf-8")
            print(f"Saved HTML to {html_path}")
        final_url = page.url
        await page.close()
        await manager.release_context(context)
    finally:
        await manager.close()

    facets = build_facet_page(final_url, raw)
    print(f"URL:     {final_url}")
    print(f"Results: {facets.total_results}")
    print(f"Links scanned: {len(raw['links'])}, inputs: {len(raw['inputs'])}")
    if not facets.groups:
        print("No filter groups found - the sidebar markup may have changed.")
    for group in facets.groups:
        kind = " [range]" if group.is_range else ""
        print(f"\n{group.title} ({group.key}){kind}: {len(group.options)} option(s)")
        for option in group.options[:8]:
            count = "" if option.count is None else f" ({option.count})"
            print(f"  - {option.label}{count} = {option.value}")
        if len(group.options) > 8:
            print(f"  … {len(group.options) - 8} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--html", type=Path, help="also save the page's HTML here")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.html))
