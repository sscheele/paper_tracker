"""Paper sources compose with existing filters before counting/pagination."""

from datetime import datetime, timezone

import pytest

from paper_downloader.arxiv import Paper
from paper_downloader.cli import main
from paper_downloader.db import PaperDB
from paper_downloader.web import create_app


@pytest.fixture
def library(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("authors: ['Hinton, Geoffrey']\n")
    db_path = tmp_path / "papers.db"
    db = PaperDB(db_path)
    date = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for aid, title, authors, category in [
        ("1", "Author paper", ["Geoffrey Hinton"], "cs.AI"),
        ("2", "Other saved paper", ["Someone Else"], "cs.LG"),
        ("3", "General top paper", ["Alice Hintonson"], "cs.LG"),
        ("4", "Tracked top paper", ["Someone Else", "G. HINTON"], "cs.AI"),
    ]:
        db.upsert_paper(Paper(aid, title, authors, "Abstract", date, date,
                              [category], "https://example.com/pdf", "https://example.com/abs"))
    for aid in ["3", "4"]:
        db.tag_paper(aid, "top-conference")
        db.tag_paper(aid, "ICLR 2025")
    db.tag_paper("4", "review")
    db.mark_read("4")
    db.toggle_favorite("4")
    try:
        yield db, db_path, config_path
    finally:
        db.close()


@pytest.mark.parametrize("view,expected", [
    ("all", ["1", "3", "2", "4"]),
    ("authors", ["1", "2", "4"]),
    ("top", ["3", "4"]),
])
def test_query_views_and_pagination(library, view, expected):
    db, _, _ = library
    options = dict(view=view, tracked_authors=["Hinton, Geoffrey"], sort_by="title", sort_order="asc")
    papers, total = db.query_papers(**options)
    assert [p["arxiv_id"] for p in papers] == expected
    assert total == len(expected)
    page, total = db.query_papers(**options, limit=1, offset=1)
    assert [p["arxiv_id"] for p in page] == expected[1:2]
    assert total == len(expected)


@pytest.mark.parametrize("authors,expected", [
    ([], {"1", "2"}),
    (["  "], {"1", "2"}),
    (["geoffrey hinton"], {"1", "2", "4"}),
    (["Hinton, Geoffrey"], {"1", "2", "4"}),
    (["Hintonson"], {"1", "2", "3"}),
])
def test_configured_author_matching(library, authors, expected):
    db, _, _ = library
    papers, total = db.query_papers(view="authors", tracked_authors=authors)
    assert {p["arxiv_id"] for p in papers} == expected
    assert total == len(expected)


@pytest.mark.parametrize("view,expected", [
    (None, {"1", "2", "3", "4"}),
    ("all", {"1", "2", "3", "4"}),
    ("authors", {"1", "2", "4"}),
    ("top", {"3", "4"}),
])
def test_list_api_views(library, view, expected):
    _, db_path, config_path = library
    with create_app(db_path, config_path).test_client() as client:
        response = client.get("/api/papers", query_string={"view": view} if view else {})
        assert response.status_code == 200
        assert {p["arxiv_id"] for p in response.json["papers"]} == expected
        assert response.json["total"] == len(expected)


@pytest.mark.parametrize("view", ["authors", "top"])
@pytest.mark.parametrize("filters,expected", [
    ({"tag": "ICLR 2025", "unread_only": "true"}, {"authors": set(), "top": {"3"}}),
    ({"tag": "ICLR 2025", "author": "Hinton", "category": "cs.AI"}, {"authors": {"4"}, "top": {"4"}}),
    ({"favorite_only": "true", "tag": "review"}, {"authors": {"4"}, "top": {"4"}}),
])
def test_api_filters_within_views(library, view, filters, expected):
    _, db_path, config_path = library
    with create_app(db_path, config_path).test_client() as client:
        response = client.get("/api/papers", query_string={"view": view, **filters})
        assert response.status_code == 200
        assert {p["arxiv_id"] for p in response.json["papers"]} == expected[view]
        assert response.json["total"] == len(expected[view])


def test_author_config_changes_take_effect(library):
    _, db_path, config_path = library
    with create_app(db_path, config_path).test_client() as client:
        assert client.get("/api/papers?view=authors").json["total"] == 3
        config_path.write_text("authors: []\n")
        assert client.get("/api/papers?view=authors").json["total"] == 2


def test_invalid_view(library):
    _, db_path, config_path = library
    with create_app(db_path, config_path).test_client() as client:
        response = client.get("/api/papers?view=invalid")
        assert response.status_code == 400


@pytest.mark.parametrize("view,show_read,expected", [
    (None, False, {"Author paper", "Other saved paper", "General top paper"}),
    ("authors", False, {"Author paper", "Other saved paper"}),
    ("authors", True, {"Author paper", "Other saved paper", "Tracked top paper"}),
    ("top", True, {"General top paper", "Tracked top paper"}),
])
def test_cli_show_views(library, monkeypatch, capsys, view, show_read, expected):
    _, db_path, config_path = library
    args = ["paper-downloader", "--config", str(config_path), "--db", str(db_path), "show"]
    if view:
        args.extend(["--view", view])
    if show_read:
        args.append("--all")
    monkeypatch.setattr("sys.argv", args)
    main()
    output = capsys.readouterr().out
    titles = {line[4:] for line in output.splitlines() if line.startswith("[")}
    assert titles == expected
