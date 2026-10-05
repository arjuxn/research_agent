from app import contacts as C

REG = "uni.edu"


def test_generic_mailboxes():
    assert C.is_generic("info@uni.edu")
    assert C.is_generic("hod.cse@uni.edu")
    assert C.is_generic("admissions@uni.edu")
    assert not C.is_generic("rajesh.kumar@uni.edu")


def test_role_detection():
    assert C.detect_role("Professor & Head")[1] == 0
    assert C.detect_role("Head of the Department")[1] == 0
    assert C.detect_role("Chairperson")[1] == 1
    assert C.detect_role("Professor")[1] == 2
    assert C.detect_role("Associate Professor")[1] == 3
    assert C.detect_role("Assistant Professor")[1] == 4
    assert C.detect_role("Visiting Professor")[1] == 5
    assert C.detect_role("Professor Emeritus") is None
    assert C.detect_role("Former Head of Department") is None
    assert C.detect_role("Student") is None


def test_endowed_chair_is_not_department_chair():
    assert C.detect_role("Distinguished Chair Professor")[1] == 2
    assert C.detect_role("Institute Chair Professor")[1] == 2
    assert C.detect_role("Chair, Department of Physics")[1] == 1
    assert C.detect_role("Department Chair")[1] == 1


def test_name_validation():
    assert C.valid_name(C.clean_name("Dr. Rajesh Kumar"))
    assert C.clean_name("Dr. Rajesh Kumar") == "Rajesh Kumar"
    assert not C.valid_name("Department of Physics")
    assert not C.valid_name("Contact Us")
    assert not C.valid_name("rajesh kumar")


def test_award_and_unit_names_are_not_people():
    assert not C.valid_name("Distinguished Alumni Service Award")
    assert not C.valid_name("Computer Centre")
    assert not C.valid_name("Annual Convocation Ceremony")


def test_html_table_head_ranked_first():
    html = """<table><tr><th>Name</th><th>Designation</th><th>Email</th></tr>
    <tr><td>Dr. Rajesh Kumar</td><td>Professor &amp; Head</td>
        <td><a href="mailto:rajesh.kumar@cs.uni.edu">mail</a></td></tr>
    <tr><td>Anita Rao</td><td>Assistant Professor</td><td>anita.rao@cs.uni.edu</td></tr></table>"""
    cs, _ = C.extract_html(html, "https://cs.uni.edu/faculty", REG)
    best = C.best_contact(cs)
    assert best.name == "Rajesh Kumar"
    assert best.rank == 0
    assert best.method == "HTML_TABLE"
    assert best.email == "rajesh.kumar@cs.uni.edu"


def test_generic_mailbox_never_bound_to_name():
    html = ('<div class="card"><h3>Dr. Meera Iyer</h3><p>Head of Department</p>'
            '<a href="mailto:info@cs.uni.edu">info@cs.uni.edu</a></div>')
    cs, _ = C.extract_html(html, "https://cs.uni.edu/", REG)
    best = C.best_contact(cs)
    assert best.name == "Meera Iyer" and best.email == ""


def test_foreign_domain_email_rejected():
    html = ('<div><h3>Dr. Meera Iyer</h3><p>Professor</p>'
            '<a href="mailto:meera@gmail.com">meera@gmail.com</a></div>')
    cs, _ = C.extract_html(html, "https://cs.uni.edu/", REG)
    assert C.best_contact(cs).email == ""


def test_structured_cards_bind_own_email_only():
    html = """<div><div class="p"><h4>Dr. Anil Verma</h4><span>Professor</span>
    <a href="mailto:anil.verma@cs.uni.edu">e</a></div>
    <div class="p"><h4>Dr. Sara Khan</h4><span>Assistant Professor</span>
    <a href="mailto:sara.khan@cs.uni.edu">e</a></div></div>"""
    cs = {c.name: c for c in C.extract_html(html, "https://cs.uni.edu/", REG)[0]}
    assert cs["Anil Verma"].email == "anil.verma@cs.uni.edu"
    assert cs["Sara Khan"].email == "sara.khan@cs.uni.edu"


def test_text_binding_requires_name_email_match():
    good = "Prof. Rajesh Kumar\nHead of Department\nrajesh.kumar@cs.uni.edu"
    best = C.best_contact(C.finalize(C.text_contacts(good, "u", REG), REG))
    assert best.rank == 0 and best.email == "rajesh.kumar@cs.uni.edu"
    bad = "Prof. Rajesh Kumar\nHead of Department\nzzz.qqq@cs.uni.edu"
    best = C.best_contact(C.finalize(C.text_contacts(bad, "u", REG), REG))
    assert best.email == ""  # proximity alone is not enough


def test_text_professor_needs_a_matching_email():
    assert C.text_contacts("Dr. Rahul Sharma\nProfessor", "u", REG) == []   # a title alone is not enough
    assert C.text_contacts("Rahul Sharma\nProfessor", "u", REG) == []
    with_email = C.text_contacts("Rahul Sharma\nProfessor\nrahul.sharma@cs.uni.edu", "u", REG)
    assert with_email and with_email[0].email == "rahul.sharma@cs.uni.edu"


def test_text_head_needs_title_or_label_beside_name():
    assert C.text_contacts("Rahul Sharma, Head of Department", "u", REG) == []   # bare phrase
    titled = C.text_contacts("Dr. Rahul Sharma\nHead of Department", "u", REG)
    assert titled and titled[0].name == "Rahul Sharma" and titled[0].rank == 0


def test_alumnus_mention_is_not_a_contact():
    text = ("Latest\nProf. George Varghese, Distinguished Alumnus, delivers a lecture on network algorithms\n"
            "Prof. George Varghese\nProfessor of Computer Science, UCLA")
    assert C.text_contacts(text, "u", REG) == []


def test_news_line_naming_the_head_is_not_a_roster_entry():
    assert C.text_contacts("Dr. Rajesh Kumar, Head of Department, receives the award", "u", REG) == []


def test_head_label_above_name_is_accepted():
    text = "Head of Department\nAmit Kumar\nProfessor\nAlgorithms\namitk@cse.uni.edu"
    best = C.best_contact(C.finalize(C.text_contacts(text, "u", REG), REG))
    assert best.rank == 0 and best.name == "Amit Kumar" and best.email == "amitk@cse.uni.edu"


def test_obfuscated_email():
    assert C.find_emails("write to a.b [at] cs [dot] uni [dot] edu") == ["a.b@cs.uni.edu"]


def test_empty_pdf_is_safe():
    cs, text = C.extract_pdf(b"not a pdf", "https://x.edu/a.pdf", REG)
    assert cs == [] and text == ""