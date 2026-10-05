from app.website import _penalty, is_blocked, name_matches, query_name


def test_acronym_name_matches_full_title():
    assert name_matches("IIT Delhi", "Indian Institute of Technology Delhi")


def test_different_university_rejected():
    assert not name_matches("University of Delhi", "Mumbai Institute of Arts")


def test_aggregators_blocked():
    assert is_blocked("shiksha.com") and is_blocked("www.collegedunia.com")
    assert not is_blocked("iitd.ac.in")


def test_query_name_drops_deemed_noise():
    assert query_name("Bharati  vidyapeeth deemed university") == "Bharati vidyapeeth university"
    assert query_name("XYZ University (Deemed to be University)") == "XYZ University"


def test_sub_unit_site_is_penalised():
    name = "Bharati vidyapeeth university"
    main = "Bharati Vidyapeeth (Deemed to be University), Pune"
    sub = "Bharati Vidyapeeth (Deemed to be University) Centre for Distance and Online Education"
    assert name_matches(name, main) and name_matches(name, sub)
    assert _penalty(name, main) < _penalty(name, sub)