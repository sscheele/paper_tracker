"""arxiv API client with polite rate limiting.

Follows arxiv API Terms of Use:
- Max 1 request every 3 seconds
- Descriptive User-Agent with contact email
- See https://info.arxiv.org/help/api/tou.html
"""

import gzip
import io
import logging
import tarfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
import httpx
import requests

log = logging.getLogger(__name__)


@dataclass
class TexResult:
    """Result of a TeX source download attempt."""
    source: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.source is not None

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# arxiv asks for at least 3 seconds between requests
MIN_REQUEST_INTERVAL = 3.0


@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    published: datetime
    updated: datetime
    categories: list[str]
    pdf_url: str
    abs_url: str

    @property
    def primary_category(self) -> str:
        return self.categories[0] if self.categories else ""


class ArxivClient:
    def __init__(self, mailto: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            f"paper-downloader/0.1 (mailto:{mailto})"
        )
        self._last_request_time = 0.0

    def _wait(self):
        """Enforce minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _get(self, params: dict, max_retries: int = 4) -> str:
        last_error = None
        for attempt in range(max_retries):
            self._wait()
            resp = self.session.get(ARXIV_API_URL, params=params, timeout=30)
            self._last_request_time = time.monotonic()
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        delay = MIN_REQUEST_INTERVAL * (attempt + 2)
                else:
                    delay = MIN_REQUEST_INTERVAL * (attempt + 2)
                log.warning("arxiv API rate limited (429), Retry-After: %s, waiting %ds (attempt %d/%d)",
                            retry_after, delay, attempt + 1, max_retries)
                last_error = resp
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.text
        last_error.raise_for_status()

    def search_author(self, author: str, max_results: int = 50) -> list[Paper]:
        """Search for recent papers by a single author."""
        return self.search_authors([author], max_results=max_results)

    def search_authors(self, authors: list[str], max_results: int = 200) -> list[Paper]:
        """Search for recent papers by any of the given authors in a single API call."""
        if not authors:
            return []
        query = " OR ".join(f'au:"{a}"' for a in authors)
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        xml_text = self._get(params)
        return self._parse_feed(xml_text)

    def fetch_tex_source(self, arxiv_id: str, max_retries: int = 5) -> TexResult:
        """Download and extract TeX source for a paper."""
        # Use arxiv.org (not export.arxiv.org) for e-print downloads —
        # export.arxiv.org truncates large files.
        url = f"https://arxiv.org/e-print/{arxiv_id}"
        user_agent = self.session.headers["User-Agent"]
        last_error = ""

        for attempt in range(max_retries):
            self._wait()
            try:
                with httpx.stream("GET", url, timeout=httpx.Timeout(15, read=300),
                                  headers={"User-Agent": user_agent},
                                  follow_redirects=True) as resp:
                    self._last_request_time = time.monotonic()
                    if resp.status_code == 404:
                        return TexResult(None, f"arxiv returned 404 for {arxiv_id} — source may not be available")
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = int(retry_after)
                            except ValueError:
                                delay = MIN_REQUEST_INTERVAL * (attempt + 1)
                        else:
                            delay = MIN_REQUEST_INTERVAL * (attempt + 1)
                        last_error = f"rate limited (429), Retry-After: {retry_after}, waiting {delay:.0f}s"
                        log.info("TeX download for %s: %s (attempt %d/%d)", arxiv_id, last_error, attempt + 1, max_retries)
                        time.sleep(delay)
                        continue
                    resp.raise_for_status()
                    raw = resp.read()

                content_type = resp.headers.get("content-type", "")
                size_kb = len(raw) / 1024
                log.info("TeX download for %s: got %d KB (content-type: %s)", arxiv_id, size_kb, content_type)
                return _extract_tex(raw, arxiv_id)

            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
                    httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                delay = MIN_REQUEST_INTERVAL * (attempt + 1)
                last_error = f"{type(e).__name__}: {e}"
                log.warning("TeX download for %s failed (attempt %d/%d): %s, retrying in %ds",
                            arxiv_id, attempt + 1, max_retries, last_error, delay)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue
                return TexResult(None, f"download failed after {max_retries} attempts: {last_error}")

        return TexResult(None, f"download failed after {max_retries} attempts: {last_error}")

    def _parse_feed(self, xml_text: str) -> list[Paper]:
        root = ET.fromstring(xml_text)
        papers = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            paper = self._parse_entry(entry)
            if paper:
                papers.append(paper)
        return papers

    def _parse_entry(self, entry: ET.Element) -> Paper | None:
        title_el = entry.find(f"{ATOM_NS}title")
        if title_el is None or title_el.text is None:
            return None

        # Extract arxiv ID from the entry id URL
        id_el = entry.find(f"{ATOM_NS}id")
        if id_el is None or id_el.text is None:
            return None
        abs_url = id_el.text.strip()
        arxiv_id = abs_url.rsplit("/", 1)[-1]
        # Strip version suffix for canonical ID
        if arxiv_id and "v" in arxiv_id:
            base_id = arxiv_id.rsplit("v", 1)
            if base_id[1].isdigit():
                arxiv_id = base_id[0]

        title = " ".join(title_el.text.split())

        authors = []
        for author_el in entry.findall(f"{ATOM_NS}author"):
            name_el = author_el.find(f"{ATOM_NS}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        summary_el = entry.find(f"{ATOM_NS}summary")
        abstract = " ".join(summary_el.text.split()) if summary_el is not None and summary_el.text else ""

        published_el = entry.find(f"{ATOM_NS}published")
        updated_el = entry.find(f"{ATOM_NS}updated")
        pub_text = published_el.text if published_el is not None and published_el.text else ""
        upd_text = updated_el.text if updated_el is not None and updated_el.text else ""
        published = _parse_datetime(pub_text)
        updated = _parse_datetime(upd_text)

        categories = []
        primary_cat = entry.find(f"{ARXIV_NS}primary_category")
        if primary_cat is not None:
            term = primary_cat.get("term", "")
            if term:
                categories.append(term)
        for cat in entry.findall(f"{ATOM_NS}category"):
            term = cat.get("term", "")
            if term and term not in categories:
                categories.append(term)

        pdf_url = ""
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break

        return Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            published=published,
            updated=updated,
            categories=categories,
            pdf_url=pdf_url,
            abs_url=abs_url,
        )


def _extract_tex(raw: bytes, arxiv_id: str = "") -> TexResult:
    """Extract .tex content from arxiv e-print response bytes."""
    if not raw:
        return TexResult(None, f"empty response body for {arxiv_id}")

    # Try as gzipped content first
    try:
        with gzip.open(io.BytesIO(raw)) as gz:
            decompressed = gz.read()
    except gzip.BadGzipFile:
        # Not gzipped — might be raw tex or PDF
        if raw[:5] == b"%PDF-":
            return TexResult(None, f"{arxiv_id} source is PDF-only (no TeX submitted)")
        # Try as plain text
        text = raw.decode("utf-8", errors="replace")
        if "\\documentclass" in text or "\\begin{" in text:
            return TexResult(text)
        return TexResult(None, f"{arxiv_id} response is not gzip, not PDF, and doesn't look like TeX ({len(raw)} bytes, starts with {raw[:20]!r})")

    # Decompressed — try as tar archive
    try:
        tar = tarfile.open(fileobj=io.BytesIO(decompressed))
        all_files = [m.name for m in tar.getmembers() if m.isfile()]
        tex_parts = []
        for member in tar.getmembers():
            if member.isfile() and (member.name.endswith(".tex") or member.name.endswith(".bbl")):
                f = tar.extractfile(member)
                if f:
                    tex_parts.append(f"% === {member.name} ===\n" + f.read().decode("utf-8", errors="replace"))
        tar.close()
        if tex_parts:
            return TexResult("\n\n".join(tex_parts))
        return TexResult(None, f"{arxiv_id} tar archive has no .tex/.bbl files (found: {', '.join(all_files[:10])})")
    except tarfile.TarError:
        # Not a tar — single gzipped .tex file
        text = decompressed.decode("utf-8", errors="replace")
        if "\\documentclass" in text or "\\begin{" in text or len(text) > 100:
            return TexResult(text)
        return TexResult(None, f"{arxiv_id} decompressed content doesn't look like TeX ({len(decompressed)} bytes)")


def _parse_datetime(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)
