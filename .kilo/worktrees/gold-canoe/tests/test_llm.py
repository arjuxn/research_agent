from pydantic import BaseModel

from app import contacts, llm


class S(BaseModel):
    x: int = 0


def test_unavailable_llm_returns_none(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda refresh=False: False)
    assert llm.ask("hi", S) is None


def test_no_llm_call_when_no_valid_result_possible(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM must not be called")
    monkeypatch.setattr(contacts.llm, "ask", boom)
    assert contacts.llm_contacts([("https://x.edu/a", "nothing useful here")], "x.edu") == []


def test_fabricated_llm_output_is_rejected(monkeypatch):
    text = "Dr. Meera Iyer\nProfessor and Head of Department\nmeera.iyer@cs.x.edu"
    fake = contacts._LCs(contacts=[
        contacts._LC(name="Meera Iyer", role="Head of Department", email="invented@cs.x.edu"),
        contacts._LC(name="John Madeup", role="Professor", email="john@cs.x.edu"),
    ])
    monkeypatch.setattr(contacts.llm, "ask", lambda *a, **k: fake)
    out = contacts.llm_contacts([("https://cs.x.edu/", text)], "x.edu")
    assert [c.name for c in out] == ["Meera Iyer"]
    assert out[0].email == ""  # invented email never appears in the source text


def test_email_belonging_to_someone_else_is_dropped(monkeypatch):
    text = "Dr. Meera Iyer\nProfessor and Head of Department\nkjaerbye@cs.x.edu"
    fake = contacts._LCs(contacts=[
        contacts._LC(name="Meera Iyer", role="Head of Department", email="kjaerbye@cs.x.edu"),
    ])
    monkeypatch.setattr(contacts.llm, "ask", lambda *a, **k: fake)
    out = contacts.llm_contacts([("https://cs.x.edu/", text)], "x.edu")
    assert out and out[0].email == ""  # email is on the page but does not resemble her name


def test_role_not_supported_by_text_is_rejected(monkeypatch):
    text = "Dr. Meera Iyer\nLecturer\nmeera.iyer@cs.x.edu\nDr. Sam Roy\nProfessor and Head of Department"
    fake = contacts._LCs(contacts=[
        contacts._LC(name="Meera Iyer", role="Head of Department", email="meera.iyer@cs.x.edu"),
    ])
    monkeypatch.setattr(contacts.llm, "ask", lambda *a, **k: fake)
    assert contacts.llm_contacts([("https://cs.x.edu/", text)], "x.edu") == []