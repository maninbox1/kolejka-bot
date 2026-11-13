"""Loads the mon.declarant.by zone page in a headless browser and captures the data it loads.

The site only returns the vehicle lists to its own page, so instead of calling the API directly
we open the page like a normal visitor and read the JSON responses the page itself receives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from urllib.parse import urlparse

from playwright.async_api import Browser, Playwright, async_playwright

from border import CHECKPOINTS

log = logging.getLogger("kolejka.fetcher")

ZONE_URL = "https://mon.declarant.by/#/zone/{slug}"
# Small hosts (Render free = 0.1 CPU) need a lot longer than a laptop to boot Chrome + the page.
TIMEOUT_S = int(os.environ.get("FETCH_TIMEOUT_S", "180"))

BLOCKED_TYPES = {"image", "font", "media", "stylesheet"}
BLOCKED_HOSTS = ("mc.yandex.ru", "yandex.ru", "google-analytics.com", "googletagmanager.com")


class FetchError(Exception):
    pass


class BrowserFetcher:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()  # one page load at a time — keeps load on the site minimal

    async def start(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        t0 = time.monotonic()
        if not self._pw:
            self._pw = await async_playwright().start()
        # Flags keep memory low enough for small hosts (e.g. Render free: 512 MB).
        self._browser = await self._pw.chromium.launch(headless=True, args=[
            "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote",
            "--disable-extensions", "--js-flags=--max-old-space-size=128"])
        log.info("browser launched in %.1fs", time.monotonic() - t0)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, slug: str) -> tuple[dict, dict]:
        """Returns (queue_json, statistics_json) as received by the zone page."""
        async with self._lock:
            try:
                return await asyncio.wait_for(self._fetch(slug), timeout=TIMEOUT_S)
            except asyncio.TimeoutError:
                raise FetchError(f"no data within {TIMEOUT_S}s — see logs") from None

    async def _fetch(self, slug: str) -> tuple[dict, dict]:
        cp_id = CHECKPOINTS[slug]["id"]
        t0 = time.monotonic()
        el = lambda: f"{time.monotonic() - t0:5.1f}s"  # noqa: E731

        await self.start()  # lazy: browser launches on first request (and relaunches if it died)
        ctx = await self._browser.new_context(
            viewport={"width": 1366, "height": 900}, locale="ru-RU", timezone_id="Europe/Minsk")

        async def route(r):
            req = r.request
            host = urlparse(req.url).hostname or ""
            if req.resource_type in BLOCKED_TYPES or host.endswith(BLOCKED_HOSTS):
                await r.abort()
            else:
                await r.continue_()
        await ctx.route("**/*", route)

        page = await ctx.new_page()
        loop = asyncio.get_running_loop()
        got: dict[str, asyncio.Future] = {"queue": loop.create_future(), "stats": loop.create_future()}

        async def on_response(resp):
            host = urlparse(resp.url).hostname or ""
            if "belarusborder" in host or "declarant" in host and "/api/" in resp.url:
                log.info("[%s] %s %s %s -> %s", slug, el(), resp.request.method,
                         urlparse(resp.url).path, resp.status)
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
        page.on("requestfailed", lambda r: None if r.failure == "net::ERR_FAILED" else log.warning(
            "[%s] %s request failed %s: %s", slug, el(), r.url[:120], r.failure))
        page.on("console", lambda m: log.warning("[%s] console: %s", slug, m.text[:200])
                if m.type == "error" and "ERR_FAILED" not in m.text and "%c" not in m.text else None)
        page.on("pageerror", lambda e: log.warning("[%s] page error: %s", slug, str(e)[:200]))

        try:
            await page.goto(ZONE_URL.format(slug=slug), wait_until="commit",
                            timeout=TIMEOUT_S * 1000)
            log.info("[%s] %s page opened", slug, el())
            queue, stats = await asyncio.gather(got["queue"], got["stats"])
            log.info("[%s] %s data received", slug, el())
            return queue, stats
        except asyncio.CancelledError:
            log.warning("[%s] %s timed out; received queue=%s stats=%s", slug, el(),
                        *(f.done() and not f.cancelled() for f in (got["queue"], got["stats"])))
            raise
        finally:
            await ctx.close()
