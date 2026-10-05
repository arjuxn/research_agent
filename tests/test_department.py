from app.department import discipline_terms, score_candidate, term_match


def test_alias_in_subdomain_scores_high():
    terms = discipline_terms("Computer Science")
    assert score_candidate(terms, "https://cse.iitd.ac.in/", "Computer Science & Engineering") >= 85


def test_program_page_scores_below_department():
    terms = discipline_terms("Computer Science")
    dept = score_candidate(terms, "https://x.edu/departments/computer-science")
    prog = score_candidate(terms, "https://x.edu/admissions/btech-computer-science")
    assert dept > prog


def test_unrelated_page_scores_low():
    terms = discipline_terms("Computer Science")
    assert score_candidate(terms, "https://x.edu/departments/history") < 50


def test_discipline_gate_rejects_look_alikes():
    terms = discipline_terms("Computer Science")
    assert not term_match(terms, "https://www.cc.iitb.ac.in/", "Computer Centre")
    assert not term_match(terms, "https://vrl.annauniv.edu/", "Computing")
    assert not term_match(terms, "https://web.mit.edu/campus-life/", "Campus Life")
    assert not term_match(terms, "https://mu.ac.in/old/science-technology.html", "Science & Technology")


def test_discipline_gate_accepts_real_departments():
    terms = discipline_terms("Computer Science")
    assert term_match(terms, "https://cse.iitb.ac.in/", "")
    assert term_match(terms, "https://x.edu/dept", "Department of Electrical Engineering and Computer Science")