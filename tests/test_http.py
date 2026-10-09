from app import http


def _page(status, url="https://x.edu/"):
    return http.Page(url, status, b"<html>x</html>", "text/html")


def test_blocked_site_falls_back_to_a_real_browser(monkeypatch):
    rendered = _page(200)
    monkeypatch.setattr(http, "fetch", lambda url, **k: _page(403))
    monkeypatch.setattr(http, "fetch_rendered", lambda url: rendered)
    assert http.fetch_robust("https://x.edu/") is rendered


def test_unreachable_site_falls_back_to_a_real_browser(monkeypatch):
    rendered = _page(200)
    monkeypatch.setattr(http, "fetch", lambda url, **k: None)
    monkeypatch.setattr(http, "fetch_rendered", lambda url: rendered)
    assert http.fetch_robust("https://x.edu/") is rendered


def test_a_missing_page_is_never_rendered(monkeypatch):
    def boom(url):
        raise AssertionError("a 404 must not trigger the browser")

    monkeypatch.setattr(http, "fetch", lambda url, **k: _page(404, "https://x.edu/a"))
    monkeypatch.setattr(http, "fetch_rendered", boom)
    assert http.fetch_robust("https://x.edu/a").status == 404


def test_a_normal_page_is_returned_as_is(monkeypatch):
    ok = _page(200)
    monkeypatch.setattr(http, "fetch", lambda url, **k: ok)
    monkeypatch.setattr(http, "fetch_rendered", lambda url: (_ for _ in ()).throw(AssertionError("no render")))
    assert http.fetch_robust("https://x.edu/") is ok


def test_if_the_browser_fails_too_the_original_block_is_returned(monkeypatch):
    blocked = _page(403)
    monkeypatch.setattr(http, "fetch", lambda url, **k: blocked)
    monkeypatch.setattr(http, "fetch_rendered", lambda url: None)
    assert http.fetch_robust("https://x.edu/") is blocked


def test_bot_protection_pages_are_recognised():
    assert http.looks_blocked("<html><head><title>Access Denied</title></head><body>No permission</body></html>")
    assert http.looks_blocked("<html><head><title>Just a moment...</title></head><body>Checking</body></html>")


def test_a_long_real_page_is_never_flagged():
    long_page = "<html><head><title>Computer Science</title></head><body>" + "welcome to the department " * 200 + "</body></html>"
    assert not http.looks_blocked(long_page)