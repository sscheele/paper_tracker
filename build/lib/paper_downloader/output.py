"""Output formatters: plaintext, HTML, RSS."""

import html
import sqlite3
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring


def format_plaintext(papers: list[sqlite3.Row], show_abstract: bool = False) -> str:
    if not papers:
        return "No papers found.\n"
    lines = []
    for p in papers:
        status = " " if p["read"] else "*"
        lines.append(f"[{status}] {p['title']}")
        lines.append(f"    Authors: {p['authors']}")
        lines.append(f"    Date: {p['published'][:10]}")
        lines.append(f"    Categories: {p['categories']}")
        lines.append(f"    {p['abs_url']}")
        if show_abstract and p["abstract"]:
            lines.append(f"    Abstract: {p['abstract']}")
        lines.append("")
    return "\n".join(lines)


def format_html(papers: list[sqlite3.Row], title: str = "Paper Digest") -> str:
    if not papers:
        return _html_page(title, "<p>No papers found.</p>")

    items = []
    for p in papers:
        read_class = "read" if p["read"] else "unread"
        escaped_title = html.escape(p["title"])
        escaped_authors = html.escape(p["authors"])
        escaped_abstract = html.escape(p["abstract"] or "")
        categories = html.escape(p["categories"] or "")
        items.append(f"""
        <div class="paper {read_class}">
            <h3><a href="{html.escape(p['abs_url'])}">{escaped_title}</a></h3>
            <p class="meta">
                <span class="authors">{escaped_authors}</span><br>
                <span class="date">{p['published'][:10]}</span>
                &middot; <span class="categories">{categories}</span>
                {' &middot; <span class="status-read">read</span>' if p["read"] else ''}
            </p>
            <p class="abstract">{escaped_abstract}</p>
            <p class="links">
                <a href="{html.escape(p['abs_url'])}">abs</a>
                {f' | <a href="{html.escape(p["pdf_url"])}">pdf</a>' if p["pdf_url"] else ""}
            </p>
        </div>""")

    return _html_page(title, "\n".join(items))


def _html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.3em; }}
  .paper {{ margin-bottom: 1.5em; padding: 1em; border: 1px solid #ddd; border-radius: 6px; }}
  .paper.unread {{ border-left: 4px solid #0066cc; }}
  .paper.read {{ opacity: 0.7; }}
  .meta {{ color: #555; font-size: 0.9em; }}
  .authors {{ font-weight: 600; }}
  .status-read {{ color: #090; font-style: italic; }}
  .abstract {{ font-size: 0.95em; line-height: 1.5; }}
  .links a {{ text-decoration: none; color: #0066cc; }}
  .links a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
{body}
</body>
</html>"""


def format_rss(papers: list[sqlite3.Row], title: str = "Paper Digest",
               link: str = "https://arxiv.org") -> str:
    """Generate an RSS 2.0 feed."""
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "link").text = link
    SubElement(channel, "description").text = f"New papers from tracked authors"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for p in papers:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = p["title"]
        SubElement(item, "link").text = p["abs_url"]
        SubElement(item, "description").text = p["abstract"] or ""
        SubElement(item, "author").text = p["authors"]
        SubElement(item, "guid", isPermaLink="true").text = p["abs_url"]
        # Parse the stored ISO date for RSS format
        try:
            dt = datetime.fromisoformat(p["published"])
            SubElement(item, "pubDate").text = dt.strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
        except (ValueError, TypeError):
            pass
        for cat in (p["categories"] or "").split(", "):
            if cat.strip():
                SubElement(item, "category").text = cat.strip()

    xml_bytes = tostring(rss, encoding="unicode", xml_declaration=False)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_bytes}'
