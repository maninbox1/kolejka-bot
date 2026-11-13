"""Loads the mon.declarant.by zone page in a headless browser and captures the data it loads.

The site only returns the vehicle lists to its own page, so instead of calling the API directly
we open the page like a normal visitor and read the JSON responses the page itself receives.
"""

from __future__ import annotations

import asyncio
import json
import logging

from playwright.async_api import Browser, Playwright, async_playwright

from border import CHECKPOINTS

log = logging.getLogger("kolejka.fetcher")

ZONE_URL = "https://mon.declarant.by/#/zone/{slug}"
TIMEOUT_S = 30


class BrowserFetcher:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()  # one page load at a time — keeps load on the site minimal

    async def start(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        if not self._pw:
            self._pw = await async_playwright().start()
        # Flags keep memory low enough for small hosts (e.g. Render free: 512 MB).
        self._browser = await self._pw.chromium.launch(headless=True, args=[
            "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote",
            "--disable-extensions", "--js-flags=--max-old-space-size=128"])

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, slug: str) -> tuple[dict, dict]:
        """Returns (queue_json, statistics_json) as received by the zone page."""
        cp_id = CHECKPOINTS[slug]["id"]
        async with self._lock:
            await self.start()  # lazy: browser launches on first request (and relaunches if it died)
            ctx = await self._browser.new_context(
                viewport={"width": 1366, "height": 900}, locale="ru-RU", timezone_id="Europe/Minsk")
            # Don't download images/fonts/media — we only need the page's data requests.
            await ctx.route("**/*", lambda route: route.abort()
                            if route.request.resource_type in ("image", "font", "media")
                            else route.continue_())
            page = await ctx.new_page()
            loop = asyncio.get_running_loop()
            got: dict[str, asyncio.Future] = {"queue": loop.create_future(), "stats": loop.create_future()}

            async def on_response(resp):
                if resp.request.method != "GET" or cp_id not in resp.url:
                    return
                key = ("stats" if "/monitoring/statistics" in resp.url
                       else "queue" if "/monitoring-new" in resp.url else None)
                if not key or got[key].done():
                    return
                try:
                    data = json.loads(await resp.text())
                except Exception:
                    return  # empty/preflight body — wait for the real one
                if not got[key].done():
                    got[key].set_result(data)

            page.on("response", on_response)
            try:
                await page.goto(ZONE_URL.format(slug=slug), wait_until="domcontentloaded")
                queue, stats = await asyncio.wait_for(
                    asyncio.gather(got["queue"], got["stats"]), timeout=TIMEOUT_S)
            finally:
                await ctx.close()
        return queue, stats
