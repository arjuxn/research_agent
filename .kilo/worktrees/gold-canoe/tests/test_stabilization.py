import json
from pathlib import Path

from typer.testing import CliRunner

import main
from app import config, pipeline
from app.models import Result


def test_batch_parser_uses_row_discipline_and_default(tmp_path):
    p = tmp_path / "universities.csv"
    p.write_text(
        "University Name,GEO,Country/Region,Discipline,Website\n"
        "A University,Delhi,India,Computer Science,https://a.edu\n"
        "B University,Mumbai,India,,https://b.edu\n"
        "No Discipline,Nowhere,India,,\n",
        encoding="utf-8",
    )
    items = main.load_batch_items(p, "Mathematics")
    assert [it["discipline"] for it in items] == ["Computer Science", "Mathematics", "Mathematics"]
    assert items[0]["website"] == "https://a.edu"


def test_user_provided_website_skips_discovery_and_writes_debug(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)

    def no_resolver(*args, **kwargs):
        raise AssertionError("website resolver should not run when website is provided")

    monkeypatch.setattr(pipeline, "resolve_website", no_resolver)
    monkeypatch.setattr(pipeline, "find_department", lambda *a, **k: None)

    res = pipeline.run_university("Example University", "Computer Science", website="example.edu")
    assert res.status == "Field Not Found"
    assert res.college_website == "https://example.edu"
    assert res.debug_file

    payload = json.loads(Path(res.debug_file).read_text(encoding="utf-8"))
    assert payload["university"] == "Example University"
    assert any(e["stage"] == "website" and e["status"] == "provided" for e in payload["events"])
    assert any(e["stage"] == "department" and e["status"] == "not_found" for e in payload["events"])


def test_interactive_menu_dispatches_single_mode(monkeypatch):
    answers = iter(["1", "Example University", "Computer Science", "", "", "https://example.edu"])
    captured = {}

    monkeypatch.setattr(main.typer, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(
        main,
        "run_university",
        lambda university, discipline, geo="", country="", website="": Result(
            university_name=university,
            discipline=discipline,
            geo=geo,
            country=country,
            college_website=website,
            status="Success",
            contact_name="Jane Doe",
        ),
    )
    monkeypatch.setattr(main, "finish", lambda results, name: captured.update({"results": results, "name": name}))

    main.interactive_menu()
    assert captured["name"] == "single"
    assert captured["results"][0].university_name == "Example University"
    assert captured["results"][0].college_website == "https://example.edu"


def test_no_args_cli_opens_interactive_menu(monkeypatch):
    called = {}
    monkeypatch.setattr(main, "interactive_menu", lambda: called.update({"ok": True}))
    res = CliRunner().invoke(main.app, [])
    assert res.exit_code == 0
    assert called["ok"]


def test_requirements_do_not_use_cloud_llm_sdks():
    reqs = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "openai" not in reqs
    assert "anthropic" not in reqs
    assert "google-generativeai" not in reqs


def test_cli_single_supports_campus_and_show_candidates(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main,
        "run_university",
        lambda university, discipline, geo="", country="", website="", campus="", show_candidates=False: (
            captured.update({"campus": campus, "show_candidates": show_candidates}),
            Result(status="Success", contact_name="Alice")
        )[1],
    )
    monkeypatch.setattr(main, "finish", lambda *a, **k: None)
    res = CliRunner().invoke(main.app, [
        "single", "Example Uni", "Computer Science", "--campus", "Pune", "--show-candidates"
    ])
    assert res.exit_code == 0
    assert captured["campus"] == "Pune"
    assert captured["show_candidates"] is True
