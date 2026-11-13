"""Conference discovery tests; all HTTP is mocked."""

import argparse
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from paper_downloader.arxiv import ArxivClient, Paper
from paper_downloader.cli import _cmd_fetch, main
from paper_downloader.config import load_config
from paper_downloader.conferences import SemanticScholarClient, fetch_conferences, resolve_venue
from paper_downloader.db import PaperDB


def response(data=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data or {}).encode()
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.Session, "get", Mock(side_effect=AssertionError("Unexpected HTTP")))
    monkeypatch.setattr("paper_downloader.conferences.time.sleep", Mock())


@pytest.mark.parametrize("venue,expected", [
    ("ICLR", ("ICLR", "ICLR")),
    ("icml", ("International Conference on Machine Learning", "ICML")),
    ("International Conference on Machine Learning", ("International Conference on Machine Learning", "ICML")),
    ("neurips", ("NeurIPS", "NeurIPS")),
    ("corl", ("CoRL", "CoRL")),
    ("icra", ("IEEE International Conference on Robotics and Automation", "ICRA")),
    ("iros", ("IROS", "IROS")),
    ("humanoids", ("IEEE-RAS International Conference on Humanoid Robots", "Humanoids")),
    ("t-ro", ("IEEE Transactions on Robotics", "T-RO")),
    ("ra-l", ("IEEE Robotics and Automation Letters", "RA-L")),
    ("ijrr", ("The International Journal of Robotics Research", "IJRR")),
    ("cvpr", ("Computer Vision and Pattern Recognition", "CVPR")),
    ("iccv", ("ICCV", "ICCV")),
    ("eccv", ("European Conference on Computer Vision", "ECCV")),
    ("tpami", ("IEEE Transactions on Pattern Analysis and Machine Intelligence", "TPAMI")),
    ("ijcv", ("International Journal of Computer Vision", "IJCV")),
    ("siggraph", ("International Conference on Computer Graphics and Interactive Techniques", "SIGGRAPH")),
    ("siggraph asia", ("SIGGRAPH Asia", "SIGGRAPH Asia")),
    ("tog", ("ACM Transactions on Graphics", "TOG")),
    ("  IEEE TRANSACTIONS ON ROBOTICS  ", ("IEEE Transactions on Robotics", "T-RO")),
    ("Custom Conference", ("Custom Conference", "Custom Conference")),
])
def test_aliases(venue, expected):
    assert resolve_venue(venue) == expected


def test_parse_and_paginate():
    client = SemanticScholarClient("test-key")
    client.session.get = Mock(side_effect=[
        response({"data": [
            {"year": 2024, "externalIds": {"ArXiv": "2401.00001v3"}},
            {"year": 2024, "externalIds": None},
            {"year": 2024, "externalIds": {"DOI": "example"}},
            {"year": 2024},
        ], "token": "next-page"}),
        response({"data": [
            {"year": 2024, "externalIds": {"ArXiv": "2401.00001v1"}},
            {"year": 2024, "externalIds": {"ArXiv": "2401.00002"}},
            {"year": 2024, "externalIds": {"ArXiv": "2401.00003"}},
        ]}),
    ])
    matches, skipped = client.fetch_top("iclr", 2024, 2)
    assert matches == [("2401.00001", "ICLR"), ("2401.00002", "ICLR")]
    assert skipped == 3
    assert client.session.headers["x-api-key"] == "test-key"
    params = client.session.get.call_args.kwargs["params"]
    assert params == {
        "venue": "ICLR", "year": "2023:2024", "sort": "citationCount:desc",
        "fields": "title,venue,year,publicationDate,citationCount,externalIds", "token": "next-page",
    }


def test_empty_venue():
    client = SemanticScholarClient()
    client.session.get = Mock(return_value=response({"data": [], "total": 0}))
    assert client.fetch_top("Unknown", 2024, 10) == ([], 0)


def test_icml_uses_publication_year():
    client = SemanticScholarClient()
    client.session.get = Mock(return_value=response({"data": [
        {"year": 2024, "publicationDate": "2024-10-01", "externalIds": {"ArXiv": "2410.00001"}},
        {"year": 2024, "publicationDate": "2024-01-20", "externalIds": {"ArXiv": "2401.00001"}},
    ]}))
    assert client.fetch_top("icml", 2024, 2) == (
        [("2410.00001", "ICML"), ("2401.00001", "ICML")], 0,
    )
    params = client.session.get.call_args.kwargs["params"]
    assert params["venue"] == "International Conference on Machine Learning"
    assert params["year"] == 2024


def test_429_retry(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("paper_downloader.conferences.time.sleep", sleep)
    monkeypatch.setattr("paper_downloader.conferences.time.monotonic", lambda: 100)
    client = SemanticScholarClient()
    client.session.get = Mock(side_effect=[response(status=429), response({"data": []})])
    assert client.fetch_top("ICLR", 2024, 1) == ([], 0)
    assert client.session.get.call_count == 2
    assert sleep.call_args_list[0].args == (5,)
    assert sleep.call_args_list[1].args == (2.0,)


def test_429_exhausted(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("paper_downloader.conferences.time.sleep", sleep)
    client = SemanticScholarClient()
    client.session.get = Mock(return_value=response(status=429))
    with pytest.raises(requests.HTTPError):
        client.fetch_top("ICLR", 2024, 1)
    assert client.session.get.call_count == 4
    assert [call.args[0] for call in sleep.call_args_list if call.args[0] >= 5] == [5, 10, 20]


def test_non_transient_error_not_retried():
    client = SemanticScholarClient()
    client.session.get = Mock(return_value=response(status=400))
    with pytest.raises(requests.HTTPError):
        client.fetch_top("ICLR", 2024, 1)
    assert client.session.get.call_count == 1


def make_paper(arxiv_id):
    old_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return Paper(arxiv_id, "An old paper", ["Author"], "Abstract", old_date,
                 old_date, ["cs.AI"], "https://example.com/pdf", "https://example.com/abs")


@pytest.mark.parametrize("venue,year,expected", [
    ("ICLR", 2025, ["2410.10813", "2505.00001"]),
    ("iclr", 2024, ["2405.00001"]),
    ("ICRA", 2025, ["2410.10813", "2505.00001"]),
    ("icra", 2024, ["2405.00001"]),
    ("CVPR", 2025, ["2410.10813", "2505.00001"]),
    ("cvpr", 2024, ["2405.00001"]),
    ("NeurIPS", 2024, ["2410.10813", "2405.00001"]),
    ("CoRL", 2024, ["2410.10813", "2405.00001"]),
    ("Humanoids", 2024, ["2410.10813", "2405.00001"]),
    ("IROS", 2024, ["2410.10813", "2405.00001"]),
    ("ICCV", 2024, ["2410.10813", "2405.00001"]),
    ("T-RO", 2024, ["2410.10813", "2405.00001"]),
    ("RA-L", 2024, ["2410.10813", "2405.00001"]),
    ("IJRR", 2024, ["2410.10813", "2405.00001"]),
    ("ECCV", 2024, ["2410.10813", "2405.00001"]),
    ("TPAMI", 2024, ["2410.10813", "2405.00001"]),
    ("IJCV", 2024, ["2410.10813", "2405.00001"]),
    ("SIGGRAPH", 2024, ["2410.10813", "2405.00001"]),
    ("SIGGRAPH Asia", 2024, ["2410.10813", "2405.00001"]),
    ("TOG", 2024, ["2410.10813", "2405.00001"]),
])
def test_cli_conference_year_tags(tmp_path, monkeypatch, venue, year, expected):
    # Citation order deliberately puts next year's fall submission ahead of valid
    # matches, so filtering must happen before applying the requested quota.
    candidates = [
        {"year": 2025, "publicationDate": "2025-10-01", "citationCount": 500,
         "externalIds": {"ArXiv": "2510.00001"}},
        {"year": 2024, "publicationDate": "2024-10-14", "citationCount": 400,
         "externalIds": {"ArXiv": "2410.10813"}},
        {"year": 2024, "publicationDate": "2024-05-01", "citationCount": 300,
         "externalIds": {"ArXiv": "2405.00001"}},
        {"year": 2025, "publicationDate": "2025-05-01", "citationCount": 200,
         "externalIds": {"ArXiv": "2505.00001"}},
    ]

    def search(url, *, params, **kwargs):
        # Emulate S2's year filter, including its supported inclusive range.
        years = str(params["year"]).split(":")
        first, last = int(years[0]), int(years[-1])
        return response({"data": [p for p in candidates if first <= p["year"] <= last]})

    get = Mock(side_effect=search)
    monkeypatch.setattr(requests.Session, "get", get)
    arxiv = Mock()
    arxiv.fetch_ids.side_effect = lambda ids: [make_paper(aid) for aid in ids]
    monkeypatch.setattr("paper_downloader.conferences.ArxivClient", Mock(return_value=arxiv))
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mailto: test@example.com\n")
    db_path = tmp_path / "papers.db"
    monkeypatch.setattr("sys.argv", [
        "paper-downloader", "--config", str(config_path), "--db", str(db_path),
        "fetch-conferences", "--venue", venue, "--year", str(year), "--top", "2",
    ])
    main()
    arxiv.fetch_ids.assert_called_once_with(expected)
    db = PaperDB(db_path)
    try:
        label = f"{resolve_venue(venue)[1]} {year}"
        assert db.get_stats()["total"] == len(expected)
        for aid in expected:
            assert db.get_paper_tags(aid) == [label, "top-conference"]
    finally:
        db.close()
    assert "publicationDate" in get.call_args.kwargs["params"]["fields"].split(",")


def test_automatic_fetch_tags_old_papers_and_preserves_read(tmp_path, monkeypatch, capsys):
    db = PaperDB(tmp_path / "papers.db")
    existing, new = make_paper("2001.00001"), make_paper("2001.00002")
    db.upsert_paper(existing)
    db.mark_read(existing.arxiv_id)
    arxiv = Mock()
    arxiv.search_authors.return_value = [new]  # Too old for the author cutoff.
    arxiv.fetch_ids.return_value = [existing, new]
    monkeypatch.setattr("paper_downloader.cli.ArxivClient", Mock(return_value=arxiv))
    monkeypatch.setattr(SemanticScholarClient, "fetch_top", Mock(return_value=(
        [(existing.arxiv_id, "ICLR"), (new.arxiv_id, "ICLR")], 1,
    )))
    config = {"mailto": "test@example.com", "authors": ["Author"], "lookback_days": 1,
              "max_results_per_author": 10, "conferences": ["ICLR"],
              "conference_years": [2024], "top_papers_per_conference": 2}
    try:
        _cmd_fetch(config, db, argparse.Namespace(days=None, no_conferences=False))
        assert db.get_stats()["total"] == 2
        assert db.get_paper(existing.arxiv_id)["read"] == 1
        assert db.get_paper(new.arxiv_id)["read"] == 0
        assert db.get_paper_tags(existing.arxiv_id) == ["ICLR 2024", "top-conference"]
        assert db.get_paper_tags(new.arxiv_id) == ["ICLR 2024", "top-conference"]
        assert db.query_papers(tag="ICLR 2024")[1] == 2
        output = capsys.readouterr().err
        assert "Found 1 papers, 0 new" in output
        assert "ICLR 2024: found 3 / with-arXiv 2 / new 1 / skipped 1" in output
        _cmd_fetch(config, db, argparse.Namespace(days=None, no_conferences=False))
        assert "new 0 / skipped 1" in capsys.readouterr().err
        assert db.get_stats()["total"] == 2
    finally:
        db.close()


def test_no_conferences_flag(tmp_path, monkeypatch):
    arxiv = Mock()
    arxiv.search_authors.return_value = []
    monkeypatch.setattr("paper_downloader.cli.ArxivClient", Mock(return_value=arxiv))
    fetch = Mock()
    monkeypatch.setattr("paper_downloader.cli.fetch_conferences", fetch)
    db = PaperDB(tmp_path / "papers.db")
    try:
        _cmd_fetch({"mailto": "test@example.com", "authors": [], "lookback_days": 1,
                    "max_results_per_author": 10, "conferences": ["ICLR"]}, db,
                   argparse.Namespace(days=None, no_conferences=True))
        fetch.assert_not_called()
    finally:
        db.close()


def test_venue_failure_isolated(tmp_path, monkeypatch):
    fetch = Mock(side_effect=[requests.ConnectionError("offline"), ([], 0)])
    monkeypatch.setattr(SemanticScholarClient, "fetch_top", fetch)
    db = PaperDB(tmp_path / "papers.db")
    arxiv = Mock()
    arxiv.fetch_ids.return_value = []
    try:
        messages = list(fetch_conferences({"mailto": "test@example.com", "conferences": ["ICLR", "CoRL"],
                                            "conference_years": [2024]}, db, arxiv_client=arxiv))
        assert "Error fetching ICLR 2024: offline" in [message for message, _ in messages]
        assert messages[-1] == ("CoRL 2024: found 0 / with-arXiv 0 / new 0 / skipped 0", 0)
        assert fetch.call_count == 2
    finally:
        db.close()


def test_author_failure_still_fetches_conferences(tmp_path, monkeypatch):
    arxiv = Mock()
    arxiv.search_authors.side_effect = requests.ConnectionError("offline")
    monkeypatch.setattr("paper_downloader.cli.ArxivClient", Mock(return_value=arxiv))
    fetch = Mock(return_value=iter([]))
    monkeypatch.setattr("paper_downloader.cli.fetch_conferences", fetch)
    db = PaperDB(tmp_path / "papers.db")
    try:
        _cmd_fetch({"mailto": "test@example.com", "authors": ["Author"], "lookback_days": 1,
                    "max_results_per_author": 10, "conferences": ["ICLR"]}, db,
                   argparse.Namespace(days=None, no_conferences=False))
        fetch.assert_called_once()
    finally:
        db.close()


def test_default_years(tmp_path, monkeypatch):
    fetch = Mock(return_value=([], 0))
    monkeypatch.setattr(SemanticScholarClient, "fetch_top", fetch)
    db = PaperDB(tmp_path / "papers.db")
    arxiv = Mock()
    arxiv.fetch_ids.return_value = []
    try:
        list(fetch_conferences({"mailto": "test@example.com", "conferences": ["ICLR"]},
                               db, arxiv_client=arxiv))
        year = datetime.now(timezone.utc).year
        assert [call.args for call in fetch.call_args_list] == [("ICLR", year, 25), ("ICLR", year - 1, 25)]
    finally:
        db.close()


def test_cli_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mailto: test@example.com\nconferences: [ICLR]\n")
    monkeypatch.setattr("sys.argv", ["paper-downloader", "--config", str(config_path),
                        "--db", str(tmp_path / "papers.db"), "fetch-conferences",
                        "--venue", "CoRL", "--venue", "NeurIPS", "--year", "2024",
                        "--year", "2023", "--top", "10"])
    fetch = Mock(return_value=iter([]))
    monkeypatch.setattr("paper_downloader.cli.fetch_conferences", fetch)
    main()
    assert fetch.call_args.kwargs == {"venues": ["CoRL", "NeurIPS"], "years": [2024, 2023], "top": 10}


def test_config_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    created = load_config(path, create_if_missing=True)
    loaded = load_config(path)
    assert created == loaded
    assert loaded["conferences"] == []
    assert loaded["conference_years"] == []
    assert loaded["top_papers_per_conference"] == 25
    assert loaded["semantic_scholar_api_key"] == ""


def test_arxiv_id_batches_and_versions():
    client = ArxivClient("test@example.com")
    client._get = Mock(return_value="""<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><id>http://arxiv.org/abs/2401.00000v2</id><title>A paper</title></entry>
        <entry><id>http://arxiv.org/abs/hep-th/9901001v3</id><title>Old ID</title></entry>
        </feed>""")
    ids = [f"2401.{i:05d}v1" for i in range(51)] + ["hep-th/9901001v2"]
    papers = client.fetch_ids(ids)
    assert [paper.arxiv_id for paper in papers] == ["2401.00000", "9901001"]
    assert client._get.call_count == 2
    first, second = client._get.call_args_list
    assert first.args[0]["max_results"] == 50
    assert len(first.args[0]["id_list"].split(",")) == 50
    assert second.args[0] == {"id_list": "2401.00050,hep-th/9901001", "max_results": 2}


@pytest.mark.parametrize("author_error", [False, True])
def test_web_fetch_stream_includes_conferences(tmp_path, monkeypatch, author_error):
    pytest.importorskip("flask")
    from paper_downloader.web import create_app

    config_path = tmp_path / "config.yaml"
    config_path.write_text("mailto: test@example.com\nauthors: []\nconferences: [ICLR]\nconference_years: [2024]\n")
    db_path = tmp_path / "papers.db"
    arxiv = Mock()
    arxiv.search_authors.return_value = []
    if author_error:
        arxiv.search_authors.side_effect = requests.ConnectionError("author fetch failed")
    arxiv.fetch_ids.return_value = [make_paper("2001.00001")]
    monkeypatch.setattr("paper_downloader.web.ArxivClient", Mock(return_value=arxiv))
    monkeypatch.setattr(SemanticScholarClient, "fetch_top", Mock(return_value=([("2001.00001", "ICLR")], 0)))
    app = create_app(db_path=db_path, config_path=config_path)
    with app.test_client() as client:
        result = client.get("/api/fetch")
        assert result.status_code == 200
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
        assert events[-1] == {"type": "done", "total_new": 1}
        assert any("ICLR 2024: found 1" in event.get("message", "") for event in events)
        if author_error:
            assert any(event["type"] == "error" for event in events)
        assert client.get("/api/papers?tag=ICLR%202024").json["total"] == 1
