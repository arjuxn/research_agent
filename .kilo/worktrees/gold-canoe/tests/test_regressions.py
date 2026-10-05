from app import contacts as C
from app import department as D
from app import website as W

REG = "uni.edu"
TERMS = D.discipline_terms("Computer Science")


# ---- IIT Bombay: "Associate Head" mistaken for the head ----
def test_associate_head_is_not_head():
    assert C.detect_role("Professor and Associate Head of the Department")[1] == 2
    assert C.detect_role("Associate Head of the Department") is None
    assert C.detect_role("Deputy Head, Department of CS") is None
    assert C.detect_role("Professor & Head")[1] == 0


def test_associate_head_card_gives_a_clean_name_and_professor_rank():
    html = ('<div><h3>Associate Head of the Department Mythili Vutukuru</h3><p>Professor</p>'
            '<a href="mailto:mythili@cse.uni.edu">m</a></div>')
    best = C.best_contact(C.extract_html(html, "https://cse.uni.edu/", REG)[0])
    assert best.name == "Mythili Vutukuru" and best.rank == 2


def test_role_mailboxes_are_generic():
    assert C.is_generic("ahead@cse.uni.edu")
    assert not C.is_generic("whitehead@cse.uni.edu")


# ---- Oxford: a named chair is not a person ----
def test_named_chair_is_not_a_person(monkeypatch):
    text = "News\nThe Strachey Professor of Computing leads the group\nChristopher Strachey\nProfessor"
    fake = C._LCs(contacts=[C._LC(name="Christopher Strachey", role="Professor")])
    monkeypatch.setattr(C.llm, "ask", lambda *a, **k: fake)
    assert C.llm_contacts([("https://cs.x.edu/", text)], "x.edu") == []


def test_llm_person_needs_evidence(monkeypatch):
    fake = C._LCs(contacts=[C._LC(name="Rahul Sharma", role="Professor")])
    monkeypatch.setattr(C.llm, "ask", lambda *a, **k: fake)
    assert C.llm_contacts([("https://cs.x.edu/", "Rahul Sharma\nProfessor")], "x.edu") == []
    out = C.llm_contacts([("https://cs.x.edu/", "Dr. Rahul Sharma\nProfessor")], "x.edu")
    assert out and out[0].name == "Rahul Sharma"


# ---- Anna University: an admissions list is not a department ----
def test_admissions_list_scores_low():
    s = D.score_candidate(TERMS, "https://cac.annauniv.edu/uddetails/ud_ug_cands_2018.html", "B.E.CSE[FT]")
    assert s < 85


def test_admissions_list_is_not_a_department_home():
    html = ("<html><head><title>B.E. CSE [FT] candidates list</title></head>"
            "<body><a href='/x'>Download</a></body></html>")
    assert not D.looks_like_dept_home(html, "https://cac.annauniv.edu/uddetails/ud_ug_cands_2018.html", TERMS)


def test_department_home_with_people_link_is_accepted():
    html = ("<html><head><title>Computer Science</title></head>"
            "<body><a href='/people/faculty'>Faculty</a></body></html>")
    assert D.looks_like_dept_home(html, "https://www.cs.ox.ac.uk/", TERMS)


def test_department_on_own_subdomain_is_accepted_even_without_links():
    html = "<html><head><title>CSE</title></head><body>hello</body></html>"
    assert D.looks_like_dept_home(html, "https://homecse.iitd.ac.in/", TERMS)



# ---- Bharati Vidyapeeth: main site that blocks scripts beats a sub-unit ----
def test_unfetchable_main_site_beats_sub_unit(monkeypatch):
    import ddgs

    class FakeDDGS:
        def text(self, q, max_results=10):
            return [
                {"href": "https://www.bvducdoe.edu.in/",
                 "title": "Bharati Vidyapeeth Deemed University Centre for Distance and Online Education"},
                {"href": "https://www.bvuniversity.edu.in/",
                 "title": "Bharati Vidyapeeth (Deemed to be University), Pune"},
            ]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    monkeypatch.setattr(
        W, "_site_text",
        lambda root: ("Bharati Vidyapeeth Deemed University Centre for Distance and Online Education "
                      "bvducdoe") if "bvducdoe" in root else None)
    assert W.search_site("Bharati Vidyapeeth university", "India") == "https://www.bvuniversity.edu.in"


# ---- Multi-campus disambiguation & snippet rejection tests ----
def test_multi_campus_without_geo_returns_ambiguous(monkeypatch):
    monkeypatch.setattr(D, "get_sitemap_urls", lambda base: [])
    monkeypatch.setattr(D, "nav_links", lambda base: [
        ("https://uni.edu/pune/department-of-computer-science", "Department of Computer Science Engineering Pune"),
        ("https://uni.edu/mumbai/department-of-computer-science", "Department of Computer Science Engineering Mumbai"),
    ])
    monkeypatch.setattr(D, "probe_subdomains", lambda base, terms: [])
    dept = D.find_department("https://uni.edu", "Computer Science", geo="")
    assert dept is not None
    assert dept.via == "ambiguous"
    assert dept.name == "Ambiguous Department"


def test_multi_campus_with_geo_disambiguates(monkeypatch):
    monkeypatch.setattr(D, "get_sitemap_urls", lambda base: [])
    monkeypatch.setattr(D, "nav_links", lambda base: [
        ("https://uni.edu/pune/department-of-computer-science", "Department of Computer Science Engineering Pune"),
        ("https://uni.edu/mumbai/department-of-computer-science", "Department of Computer Science Engineering Mumbai"),
    ])
    monkeypatch.setattr(D, "probe_subdomains", lambda base, terms: [])

    from app.http import Page
    html = "<html><head><title>Department of Computer Science Pune</title></head><body><a href='/people'>Faculty</a></body></html>"
    monkeypatch.setattr(D, "fetch", lambda u, **kw: Page(url=u, status=200, content=html.encode("utf-8")))

    dept = D.find_department("https://uni.edu", "Computer Science", geo="Pune")
    assert dept is not None
    assert dept.via.startswith("deterministic-geo")
    assert "pune" in dept.url


def test_search_snippet_alone_cannot_create_department_when_fetch_fails(monkeypatch):
    import ddgs

    class FakeDDGS:
        def text(self, q, max_results=8):
            return [{
                "href": "https://uni.edu/dept/cs",
                "title": "Department of Computer Science",
                "body": "Welcome to Computer Science. Head of Department Dr. Alice Smith",
            }]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    monkeypatch.setattr(D, "fetch", lambda u, **kw: None)

    dept = D._search_fallback("uni.edu", "Computer Science", TERMS)
    assert dept is None


def test_fetched_department_page_with_hod_extracts_correct_person():
    html = (
        "<html><head><title>Department of Computer Science</title></head>"
        "<body>"
        "<h1>Department of Computer Science</h1>"
        "<div class='hod-box'>"
        "<h3>Head of Department</h3>"
        "<p>Dr. Bindu Garg</p>"
        "<p>Professor and Head</p>"
        "<a href='mailto:bindu.garg@uni.edu'>bindu.garg@uni.edu</a>"
        "</div>"
        "</body></html>"
    )
    contacts, _ = C.extract_html(html, "https://uni.edu/cs/", "uni.edu")
    best = C.best_contact(C.finalize(contacts, "uni.edu"))
    assert best is not None
    assert best.name == "Bindu Garg"
    assert best.rank == 0
    assert best.email == "bindu.garg@uni.edu"
