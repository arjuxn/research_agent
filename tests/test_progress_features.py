from app import department as D
from app import http

TERMS = D.discipline_terms("Computer Science")
TWO_CAMPUSES = [
    (90.0, "https://pune.x.edu/cse", "Computer Science Pune"),
    (88.0, "https://mumbai.x.edu/cse", "Computer Science Mumbai"),
]


def test_two_campuses_without_geo_is_ambiguous():
    assert D._pick(TWO_CAMPUSES, "Computer Science", geo="")[3] == "ambiguous"


def test_geo_picks_the_matching_campus():
    url, text, conf, via = D._pick(TWO_CAMPUSES, "Computer Science", geo="Mumbai")
    assert url == "https://mumbai.x.edu/cse" and via == "deterministic-geo"


def test_geo_in_the_url_raises_the_score():
    url = "https://x.edu/campus/mumbai/programs/computer-science"
    assert D.score_candidate(TERMS, url, "", geo="Mumbai") > D.score_candidate(TERMS, url, "")


def test_a_blocked_department_page_is_reported_not_ignored(monkeypatch):
    monkeypatch.setattr(D, "fetch_robust", lambda url: http.Page(url, 403, b"", "text/html"))
    blocked = []
    dept = D._finalize("https://x.edu/cse", "CSE", 0.9, "deterministic", TERMS, blocked_urls=blocked)
    assert dept is None
    assert blocked == ["https://x.edu/cse"]


def test_a_missing_department_page_is_not_reported_as_blocked(monkeypatch):
    monkeypatch.setattr(D, "fetch_robust", lambda url: http.Page(url, 404, b"", "text/html"))
    blocked = []
    assert D._finalize("https://x.edu/gone", "CSE", 0.9, "deterministic", TERMS, blocked_urls=blocked) is None
    assert blocked == []