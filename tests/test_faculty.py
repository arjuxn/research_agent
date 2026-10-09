from app.faculty import score_page


def test_faculty_pages_qualify():
    assert score_page("https://x.edu/faculty/", "Faculty Member") > 0
    assert score_page("https://x.edu/people/", "People") > 0
    assert score_page("https://x.edu/hod-message", "Head of the Department") > 0


def test_recognition_and_generic_pages_do_not_qualify():
    assert score_page("https://x.edu/recognitions/distinguished-chair-professors", "") == 0
    assert score_page("https://x.edu/about", "About us") == 0
    assert score_page("https://x.edu/professors-day", "Professors") == 0  # weak words alone never qualify
    assert score_page("https://x.edu/alumni/faculty-awards", "") == 0


def test_hiring_and_accreditation_documents_do_not_qualify():
    assert score_page("https://x.edu/media/pdf/Faculty_Eligibility_180723.pdf", "Faculty Eligibility") == 0
    assert score_page("https://x.edu/media/pdf/IQAC_Composition_2025-26.pdf", "IQAC Members") == 0


def test_a_real_faculty_list_pdf_still_qualifies():
    assert score_page("https://x.edu/media/pdf/Front_page_190825.pdf", "Faculty Members") > 0