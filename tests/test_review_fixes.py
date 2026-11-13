"""Regressions for conference fetching and tracked author views."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from paper_downloader.arxiv import ArxivClient, Paper
from paper_downloader.conferences import SemanticScholarClient
from paper_downloader.db import PaperDB
from paper_downloader.web import create_app


def test_config_exposes_conferences_without_authors(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mailto: test@example.com\nauthors: []\nconferences: [ICLR]\n")
    with create_app(tmp_path / "papers.db", config_path).test_client() as client:
        config = client.get("/api/config").get_json()
        assert config["authors"] == []
        assert config.get("conferences") == ["ICLR"]


@pytest.mark.parametrize("failure", [429, 503, requests.Timeout, requests.ConnectionError])
def test_fetch_ids_retry_budget(monkeypatch, failure):
    client = ArxivClient("test@example.com")
    monkeypatch.setattr("paper_downloader.arxiv.time.sleep", Mock())
    if isinstance(failure, int):
        response = requests.Response()
        response.status_code = failure
        attempts = [response] * 4
    else:
        attempts = [failure("offline")] * 4
    # Stop the old infinite loop with a diagnostic instead of hanging pytest.
    client.session.get = Mock(side_effect=attempts + [
        AssertionError("fetch_ids exceeded its 3-retry budget"),
    ])
    with pytest.raises(requests.RequestException):
        client.fetch_ids(["2401.00001"])
    assert client.session.get.call_count == 4


@pytest.mark.parametrize("failure", [429, 503, requests.Timeout, requests.ConnectionError])
def test_author_search_keeps_retrying(monkeypatch, failure):
    client = ArxivClient("test@example.com")
    monkeypatch.setattr("paper_downloader.arxiv.time.sleep", Mock())
    if isinstance(failure, int):
        response = requests.Response()
        response.status_code = failure
        attempts = [response] * 6
    else:
        attempts = [failure("offline")] * 6
    success = requests.Response()
    success.status_code = 200
    success._content = b'<feed xmlns="http://www.w3.org/2005/Atom" />'
    client.session.get = Mock(side_effect=attempts + [success])
    assert client.search_authors(["Author"]) == []
    assert client.session.get.call_count == 7


@pytest.mark.parametrize("all_fail", [False, True])
def test_conference_failures_are_error_events(tmp_path, monkeypatch, all_fail):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mailto: test@example.com\nauthors: []\n"
                           "conferences: [ICLR, CoRL]\nconference_years: [2024]\n")
    fetch_top = Mock(side_effect=[
        requests.ConnectionError("ICLR offline"),
        requests.ConnectionError("CoRL offline") if all_fail else ([("2401.00001", "CoRL")], 0),
    ])
    monkeypatch.setattr(SemanticScholarClient, "fetch_top", fetch_top)
    arxiv = Mock()
    arxiv.search_authors.return_value = []
    date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    arxiv.fetch_ids.return_value = [Paper("2401.00001", "Paper", ["Author"], "Abstract",
                                          date, date, ["cs.AI"], "pdf", "abs")]
    monkeypatch.setattr("paper_downloader.web.ArxivClient", Mock(return_value=arxiv))
    with create_app(tmp_path / "papers.db", config_path).test_client() as client:
        response = client.get("/api/fetch")
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == (2 if all_fail else 1), events
    assert errors[0]["message"] == "Error fetching ICLR 2024: ICLR offline"
    assert events[-1] == {"type": "done", "total_new": 0 if all_fail else 1}
    assert [call.args[0] for call in fetch_top.call_args_list] == ["ICLR", "CoRL"]


@pytest.mark.parametrize("tracked", ["van Beethoven, Ludwig", "  VAN   beethoven , Ludwig  "])
def test_compound_surname_matches_trailing_tokens(tmp_path, tracked):
    db = PaperDB(tmp_path / "papers.db")
    date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    try:
        for aid, authors in [
            ("1", ["Other Author", "Ludwig van Beethoven"]),
            ("2", ["LUDWIG VAN   BEETHOVEN"]),
            ("3", ["Ludwig Ivan Beethoven"]),
            ("4", ["Ludwig van Beethovenson"]),
            ("5", ["Ludwig van Beethoven Junior"]),
        ]:
            db.upsert_paper(Paper(aid, "Paper", authors, "Abstract", date, date,
                                  ["cs.AI"], "pdf", "abs"))
            db.tag_paper(aid, "top-conference")
        papers, total = db.query_papers(view="authors", tracked_authors=[tracked])
        assert {paper["arxiv_id"] for paper in papers} == {"1", "2"}
        assert total == 2
    finally:
        db.close()
