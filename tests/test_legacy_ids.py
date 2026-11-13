import xml.etree.ElementTree as ET

from paper_downloader.arxiv import ATOM_NS, ArxivClient


def _entry(abs_url: str) -> ET.Element:
    entry = ET.Element(f"{ATOM_NS}entry")
    ET.SubElement(entry, f"{ATOM_NS}id").text = abs_url
    ET.SubElement(entry, f"{ATOM_NS}title").text = "T"
    ET.SubElement(entry, f"{ATOM_NS}published").text = "1999-01-01T00:00:00Z"
    ET.SubElement(entry, f"{ATOM_NS}updated").text = "1999-01-01T00:00:00Z"
    return entry


def test_legacy_id_keeps_existing_db_key():
    # main stored legacy IDs as the last path segment; changing the key would
    # duplicate already-stored papers and orphan their read state and tags.
    paper = ArxivClient(mailto="x@example.com")._parse_entry(
        _entry("http://arxiv.org/abs/hep-th/9901001v1"))
    assert paper.arxiv_id == "9901001"


def test_modern_id_strips_version():
    paper = ArxivClient(mailto="x@example.com")._parse_entry(
        _entry("http://arxiv.org/abs/2410.10813v2"))
    assert paper.arxiv_id == "2410.10813"
