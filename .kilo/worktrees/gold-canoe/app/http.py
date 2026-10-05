"""HTTP fetching with a small on-disk cache and an optional Playwright fallback."""
import hashlib
import json
from dataclasses import dataclass

import requests
import urllib3
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from . import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en;q=0.9"})
_failed: set[str] = set()  # URLs that failed this process: don't pay the timeout twice
_pw_disabled = False       # set when Chromium is missing: don't retry on every page


@dataclass
class Page:
    url: str
    status: int
    content: bytes
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    @property
    def is_pdf(self) -> bool:
        return (
            "pdf" in self.content_type.lower()
            or self.content[:5] == b"%PDF-"
            or self.url.lower().split("?")[0].endswith(".pdf")
        )

    @property
    def text(self) -> str:
        if self.is_pdf:
            return ""
        try:
            return self.content.decode("utf-8")
        except UnicodeDecodeError:
            return self.content.decode("cp1252", errors="replace")


def _get_once(url: str, timeout):
    try:
        return _session.get(url, timeout=timeout, allow_redirects=True)
    except requests.exceptions.SSLError:
        # Many university sites have broken certificates; we only read public pages
        return _session.get(url, timeout=timeout, allow_redirects=True, verify=False)


_get_retry = retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    reraise=True,
)(_get_once)


def _cache_base(url: str):
    return config.CACHE_DIR / hashlib.sha1(url.encode()).hexdigest()


def fetch(url: str, cache: bool = True, timeout=None, retries: bool = True) -> Page | None:
    if url in _failed:
        return None
    base = _cache_base(url)
    meta_f, bin_f = base.with_suffix(".json"), base.with_suffix(".bin")
    if cache and config.HTTP_CACHE and meta_f.exists() and bin_f.exists():
        try:
            m = json.loads(meta_f.read_text())
            return Page(m["url"], m["status"], bin_f.read_bytes(), m["content_type"])
        except Exception:
            pass
    try:
        r = (_get_retry if retries else _get_once)(url, timeout or config.HTTP_TIMEOUT)
    except Exception as e:
        logger.debug(f"fetch failed {url}: {str(e)[:150]}")
        _failed.add(url)
        return None
    if len(r.content) > config.MAX_DOWNLOAD_BYTES:
        return None
    page = Page(r.url, r.status_code, r.content, r.headers.get("Content-Type", ""))
    if cache and config.HTTP_CACHE and page.ok:
        bin_f.write_bytes(page.content)
        meta_f.write_text(json.dumps({"url": page.url, "status": page.status,
                                      "content_type": page.content_type}))
    return page


_rendered_cache: dict[str, Page | None] = {}


def fetch_rendered(url: str) -> Page | None:
    """Playwright fallback for JS-rendered pages. Returns None if Playwright/Chromium is missing.
    Waits for the DOM, gives the network a short chance to go quiet, then reads whatever is there:
    busy sites never reach 'networkidle', so we must not wait for it."""
    global _pw_disabled
    if _pw_disabled:
        return None
    if url in _rendered_cache:
        return _rendered_cache[url]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _pw_disabled = True
        logger.warning("playwright is not installed; JS-only pages will be skipped")
        return None
    page = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=config.USER_AGENT, ignore_https_errors=True)
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                pg.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass  # busy site: take what has rendered so far
            pg.wait_for_timeout(1_500)
            html = pg.content()
            browser.close()
        page = Page(url, 200, html.encode("utf-8"), "text/html")
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            _pw_disabled = True
            logger.warning("Chromium is not installed. Run: playwright install chromium "
                           "(JS-only pages will be skipped until then)")
        else:
            logger.warning(f"rendered fetch failed for {url}: {msg[:200]}")
    _rendered_cache[url] = page
    return page