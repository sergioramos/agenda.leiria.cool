"""Headless-browser fetch for the few hosts behind Cloudflare/JS (e.g. Tickettailor),
where the requests-based crawler gets a 403 and the image is therefore unreachable.

Used ONLY as a fallback, and ONLY for hosts listed in crawl.browser_hosts, bounded
per run by crawl.browser_fetch_cap. It is lazy and OPTIONAL: if Playwright or its
Chromium isn't installed (e.g. a local run), fetch_html returns None and the caller
falls back to no image — it never crashes the crawl. One browser is launched on
first use and reused; atexit closes it.

This is the deliberate exception to "requests-only": a real browser is the only
thing that passes Cloudflare's JS challenge without fragile TLS-impersonation. It
adds a Chromium install to the crawl job (~1 min) — see crawl-events.yml.
"""
from __future__ import annotations

import atexit
from urllib.parse import urlparse

_CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_pw = None
_browser = None
_fetched = 0
_unavailable = False


def needs_browser(url: str, cfg: dict) -> bool:
    """True when url's host is one we know needs a real browser (Cloudflare/JS)."""
    host = urlparse(url or "").netloc.lower()
    hosts = (cfg.get("crawl", {}) or {}).get("browser_hosts") or []
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


def fetch_html(url: str, cfg: dict) -> str | None:
    """Render url in headless Chromium and return its HTML, or None (Playwright/
    Chromium missing, cap reached, or a page error). Reuses one browser."""
    global _pw, _browser, _fetched, _unavailable
    if _unavailable:
        return None
    crawl = cfg.get("crawl", {}) or {}
    if _fetched >= crawl.get("browser_fetch_cap", 40):
        return None
    try:
        if _browser is None:
            from playwright.sync_api import sync_playwright
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(headless=True)
    except Exception:
        _unavailable = True   # not installed in this environment — stop trying
        return None
    page = None
    host = urlparse(url).netloc
    try:
        _fetched += 1
        page = _browser.new_page(user_agent=_CHROME_UA)
        page.goto(url, wait_until="domcontentloaded",
                  timeout=max(crawl.get("per_source_timeout_s", 25), 30) * 1000)
        page.wait_for_timeout(crawl.get("browser_wait_ms", 4000))  # let the JS/CF challenge resolve
        html = page.content()
        # surface whether CI actually clears Cloudflare — a challenge/"Just a moment"
        # page means the datacenter IP is blocked (it passes from a residential IP).
        low = html.lower()
        blocked = len(html) < 8000 and ("just a moment" in low or "challenge-platform" in low
                                        or "attention required" in low or "cf-" in low)
        print(f"[browser] {host} -> {'BLOCKED (Cloudflare challenge)' if blocked else f'ok ({len(html)} bytes)'}")
        return None if blocked else html
    except Exception as e:
        print(f"[browser] {host} -> failed ({type(e).__name__})")
        return None
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def close() -> None:
    global _pw, _browser
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browser = _pw = None


atexit.register(close)
