# paper-downloader

Track new arXiv papers from your favorite researchers. Fetches papers via the arXiv API, stores them locally in SQLite, and lets you browse, tag, and annotate them from the command line or a web UI.

Note: although this is called paper_downloader it actually isn't intended to download papers as a primary use case. Sorry for the poor naming!

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
```

## Usage

### Fetch new papers

```bash
paper-downloader fetch            # uses configured lookback_days
paper-downloader fetch --days 14  # override lookback period
```

### Browse papers

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
