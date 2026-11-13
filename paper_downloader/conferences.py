"""Discover highly cited conference papers through Semantic Scholar."""

import time
from datetime import datetime, timezone

import requests

from .arxiv import ArxivClient, normalize_arxiv_id
from .db import PaperDB

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
VENUE_ALIASES = {
    "ICLR": "ICLR",
    "NeurIPS": "NeurIPS",
    "CoRL": "CoRL",
}
MIN_REQUEST_INTERVAL = 2.0
# These venues receive fall submissions for the following spring's conference.
PRIOR_YEAR_SUBMISSION_VENUES = {"ICLR"}


def resolve_venue(venue: str) -> tuple[str, str]:
    """Return the S2 venue filter and canonical short label; pass unknowns through."""
    venue = venue.strip()
    for label, name in VENUE_ALIASES.items():
        if venue.casefold() in (label.casefold(), name.casefold()):
            return name, label
    return venue, venue


class SemanticScholarClient:
    def __init__(self, api_key: str = ""):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "paper-downloader/0.1"
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self._last_request_time = 0.0

    def _get(self, params: dict) -> dict:
        """Space requests and retry transient failures four times in total."""
        for attempt in range(4):
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)
            try:
                response = self.session.get(S2_SEARCH_URL, params=params, timeout=(10, 60))
                self._last_request_time = time.monotonic()
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                self._last_request_time = time.monotonic()
                status = e.response.status_code if e.response is not None else None
                if attempt == 3 or (status is not None and status != 429 and status < 500):
                    raise
                time.sleep(5 * 2 ** attempt)

    def fetch_top(self, venue: str, year: int, top: int) -> tuple[list[tuple[str, str]], int]:
        """Return up to top unique arXiv IDs with labels, plus non-arXiv skips.

        Walk citation-sorted pages until enough arXiv papers are found or the
        venue is exhausted. Only count papers inspected to fill that quota.
        """
        if top < 1:
            raise ValueError("top_papers_per_conference must be positive")
        name, label = resolve_venue(venue)
        prior_year_submissions = label in PRIOR_YEAR_SUBMISSION_VENUES
        params = {
            "venue": name,
            "year": f"{year - 1}:{year}" if prior_year_submissions else year,
            "sort": "citationCount:desc",
            "fields": "title,venue,year,publicationDate,citationCount,externalIds",
        }
        papers = []
        seen = set()
        skipped = 0
        while True:
            result = self._get(params)
            for paper in result.get("data", []):
                if prior_year_submissions:
                    conference_year = paper.get("year")
                    publication_date = paper.get("publicationDate")
                    if conference_year is not None and publication_date:
                        if int(publication_date[5:7]) >= 9:
                            conference_year += 1
                    if conference_year != year:
                        continue
                arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
                if not arxiv_id:
                    skipped += 1
                    continue
                arxiv_id = normalize_arxiv_id(arxiv_id)
                if arxiv_id in seen:
                    continue
                seen.add(arxiv_id)
                papers.append((arxiv_id, label))
                if len(papers) == top:
                    return papers, skipped
            token = result.get("token")
            if not token:
                return papers, skipped
            params["token"] = token


def fetch_conferences(config: dict, db: PaperDB, *, venues=None, years=None, top=None,
                      arxiv_client: ArxivClient | None = None):
    """Yield (progress message, new count) for each configured venue/year.

    Conference discovery deliberately has no lookback cutoff. Failures are
    reported per venue/year so other conferences and author discovery continue.
    """
    venues = config.get("conferences", []) if venues is None else venues
    current_year = datetime.now(timezone.utc).year
    years = (config.get("conference_years") or [current_year, current_year - 1]) if years is None else years
    top = config.get("top_papers_per_conference", 25) if top is None else top
    if top < 1:
        raise ValueError("top_papers_per_conference must be positive")
    scholar = SemanticScholarClient(config.get("semantic_scholar_api_key") or "")
    arxiv = arxiv_client or ArxivClient(mailto=config["mailto"])
    for venue in venues:
        for year in years:
            label = f"{resolve_venue(venue)[1]} {year}"
            yield f"Fetching {label}...", 0
            try:
                matches, skipped = scholar.fetch_top(venue, year, top)
                papers = arxiv.fetch_ids([aid for aid, _ in matches])
                total_new = 0
                for paper in papers:
                    if db.upsert_paper(paper):
                        total_new += 1
                    db.tag_paper(paper.arxiv_id, label)
                    db.tag_paper(paper.arxiv_id, "top-conference")
                missing = len(matches) - len(papers)
                yield (f"{label}: found {len(matches) + skipped} / with-arXiv {len(matches)} / "
                       f"new {total_new} / skipped {skipped + missing}"), total_new
            except Exception as e:
                yield f"Error fetching {label}: {e}", 0
