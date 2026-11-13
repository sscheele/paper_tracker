# paper-downloader

Track new arXiv papers from your favorite researchers. Fetches papers via the arXiv API, stores them locally in SQLite, and lets you browse, tag, and annotate them from the command line or a web UI.

Note: although this is called paper_downloader it actually isn't intended to download papers as a primary use case. Sorry for the poor naming!

Thank you to arXiv for use of its open access interoperability!

## Installation

Requires Python 3.10+.

```bash
# Clone and install
git clone <repo-url>
cd paper_downloader
uv pip install -e .

# With the web UI
uv pip install -e '.[web]'
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync            # CLI only
uv sync --extra web  # with web UI
```

## Configuration

Copy the example config and edit it:

```bash
mkdir -p ~/.config/paper-downloader
cp config.example.yaml ~/.config/paper-downloader/config.yaml
```

```yaml
# Required — arXiv asks for a contact email in the User-Agent.
mailto: you@example.com

# Authors to track (names as they appear on arXiv).
authors:
  - "Hinton, Geoffrey"
  - "Bengio, Yoshua"
  - "LeCun, Yann"

lookback_days: 7           # how far back to search (default: 7)
max_results_per_author: 50 # max results per author query (default: 50)
conferences: []           # e.g. [ICRA, CVPR, SIGGRAPH, IROS]; included in normal fetch
conference_years: []      # empty = current year and previous year
top_papers_per_conference: 25
semantic_scholar_api_key: "" # optional; helps with API rate limits
```

## Usage

### Fetch new papers

```bash
paper-downloader fetch            # uses configured lookback_days
paper-downloader fetch --days 14  # override lookback period
paper-downloader fetch --no-conferences # only fetch authors
paper-downloader fetch-conferences # use configured conferences/years/top count
paper-downloader fetch-conferences --venue ICLR --venue NeurIPS --venue CoRL --year 2024 --top 10
```

Conference discovery uses the [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph)
to select the most cited papers with arXiv IDs, then fetches canonical metadata
from arXiv. Results enter the same database and receive tags such as `ICLR 2024`
for the web UI's tag filter, plus the fixed `top-conference` tag. Existing read
status is preserved. Conference papers ignore `lookback_days`; `--venue` and
`--year` can be repeated for ad-hoc runs.
The web UI's fetch also includes configured conferences.

Built-in venue labels (case insensitive; full S2 venue names also work):

- ML: `ICLR`, `ICML` (International Conference on Machine Learning), `NeurIPS`, `CoRL`.
- Robotics conferences: `ICRA`, `IROS`, `Humanoids`.
- Robotics journals: `T-RO` (IEEE Transactions on Robotics), `RA-L` (IEEE Robotics
  and Automation Letters), `IJRR` (The International Journal of Robotics Research).
- Computer vision: `CVPR`, `ICCV`, `ECCV`, `TPAMI` (IEEE Transactions on Pattern
  Analysis and Machine Intelligence), `IJCV` (International Journal of Computer Vision).
- Graphics: `SIGGRAPH`, `SIGGRAPH Asia`, `TOG` (ACM Transactions on Graphics).

`SIGGRAPH` uses S2's `International Conference on Computer Graphics and Interactive
Techniques` filter; `SIGGRAPH Asia` uses its distinct `SIGGRAPH Asia` filter.
These return conference-track technical papers. Add `TOG` for the journal-track
papers, which [SIGGRAPH publishes in ACM Transactions on Graphics](https://s2024.siggraph.org/program/technical-papers/),
alongside other TOG articles. TOG uses the publication year, without a conference
year shift. Unknown venue names pass through to S2.

RSS is omitted: the tested S2 venue spellings return unrelated statistics papers
for 2025, despite returning robotics papers for 2024. Science Robotics is omitted
for low arXiv coverage (2 of its top 20 citation-ranked 2024 papers).

Per-venue summaries report unique papers inspected (`found`), those with arXiv
IDs (`with-arXiv`), newly inserted papers (`new`), and papers without arXiv IDs
or missing arXiv metadata (`skipped`). Discovery scans further down the citation
ranking to fill the requested arXiv quota when possible. S2's `year` is publication
year. `--year` and `conference_years` select the conference year: for ICLR, ICRA,
and CVPR, we query S2 years Y-1 and Y and infer the conference year as S2 year + 1 for
September–December publication dates, otherwise S2 year. Missing dates fall back
to S2 year. This inference can still mislabel early-posted preprints; coverage
can be incomplete. All other venues, including ICML and journals, use S2 year directly.
ICRA has a [September submission deadline](https://2025.ieee-icra.org/contribute/)
for its May conference; CVPR has a
[November submission deadline](https://cvpr2023.thecvf.com/Conferences/2025/Dates)
for its June conference.
Unauthenticated S2 access is rate limited: requests are spaced two seconds apart,
with retries after 5, 10, and 20 seconds. An exhausted venue is reported and the
remaining venues continue; an optional API key is sent as `x-api-key`.

### Browse papers

The web UI has **My authors** and **Top papers** tabs, with the selected tab saved
in the URL hash. Top papers shows papers tagged `top-conference`; My authors shows
all other papers plus conference papers matching a tracked author's surname.
The existing filters apply within each tab. Top papers also has Venue and Year
dropdowns derived from known venue/year tags on top-conference papers. Either
can be selected independently; selections survive tab switches and are ignored
in My authors. Previously fetched conference papers
receive the new tag when fetched again. The list API defaults to all papers and
accepts `view=all`, `view=authors`, or `view=top`; CLI `show` accepts `--view` with
the same choices.

```bash
paper-downloader show               # unread papers, plaintext
paper-downloader show --all          # include already-read papers
paper-downloader show --abstract     # include abstracts
paper-downloader show --format html -o papers.html
paper-downloader show --format rss  -o feed.xml
```

### Manage read status

```bash
paper-downloader read 2301.00001 2301.00002   # mark as read
paper-downloader unread 2301.00001             # mark as unread
paper-downloader catchup                       # mark everything as read
```

### Stats

```bash
paper-downloader stats
```

### Web UI

```bash
paper-downloader serve                  # http://127.0.0.1:8088
paper-downloader serve --port 9000      # custom port
paper-downloader serve --debug          # auto-reload on changes
```

The web interface supports browsing, filtering by author/category/tag, favoriting, tagging, notes, bulk read/unread, and downloading TeX sources.

## Data storage

Everything lives under `~/.config/paper-downloader/`:

| File | Purpose |
|---|---|
| `config.yaml` | Configuration |
| `papers.db` | SQLite database |
