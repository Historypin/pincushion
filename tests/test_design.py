"""Tests for the archive pages' structure: provenance, navigation and the
pin record."""

from __future__ import annotations

import json

import pytest

from pincushion.archive import ArchiveGenerator, license_label
from tests.test_archive import make_archive, read


@pytest.fixture
def archive(tmp_path):
    ArchiveGenerator(make_archive(tmp_path)).generate()
    return tmp_path


PAGES = [
    ("index.html", ""),
    ("collections/3/index.html", "../../"),
    ("pins/10/index.html", "../../"),
    ("tags/index.html", "../"),
    ("tags/a-b.html", "../"),
    ("map/index.html", "../"),
]


@pytest.mark.parametrize("page, root", PAGES)
def test_every_page_says_where_the_archive_came_from(archive, page, root):
    html = read(archive, page)

    assert "Archived from hp.test on 19 November 2026" in html
    assert "pincushion 0.6.0" in html
    assert "5 pins" in html


@pytest.mark.parametrize("page, root", PAGES)
def test_every_page_links_to_the_tags_and_map(archive, page, root):
    html = read(archive, page)

    assert f'href="{root}index.html"' in html
    assert f'href="{root}tags/index.html"' in html
    assert f'href="{root}map/index.html"' in html


def test_partial_and_truncated_notices_on_every_page(tmp_path):
    archive = make_archive(
        tmp_path,
        partial={"collections_missing": 1, "pins_missing": 2},
        truncated=["pins/?creator=42"],
    )

    ArchiveGenerator(archive).generate()

    for page in ("index.html", "pins/10/index.html"):
        html = read(archive, page)
        assert "leaves out 1 collection and 2 pins" in html
        assert "10,000" in html


def test_pin_page_heading_is_the_title(archive):
    html = read(archive, "pins", "10", "index.html")

    assert "<h1>View from Twin Peaks</h1>" in html


def test_pin_record_order_and_labels(archive):
    html = read(archive, "pins", "10", "index.html")
    labels = [
        "Date",
        "Location",
        "Creator",
        "Rights",
        "License",
        "Identifier",
        "Source",
        "Tags",
        "Added to Historypin",
        "On Historypin",
    ]

    positions = [html.index(f"<dt>{label}</dt>") for label in labels]
    assert positions == sorted(positions)
    # the uploader and the attribution are told apart
    assert "Jane Photographer" in html[html.index("<dt>Creator</dt>") :]
    assert "by Ada Lovelace" in html[html.index("<dt>Added to Historypin</dt>") :]


def test_rights_statement_links_are_clickable(tmp_path):
    archive = make_archive(tmp_path)
    data = json.loads((archive / "data.json").read_text())
    data["pins"][0]["rights_statement"] = "See https://example.com/permissions"
    (archive / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(archive).generate()

    html = read(archive, "pins", "10", "index.html")
    assert '<a href="https://example.com/permissions"' in html


@pytest.mark.parametrize(
    "code, label, href",
    [
        (
            "http://creativecommons.org/licenses/by-sa/3.0/",
            "CC BY-SA 3.0",
            "http://creativecommons.org/licenses/by-sa/3.0/",
        ),
        (
            "http://creativecommons.org/publicdomain/zero/1.0/",
            "CC0 1.0",
            "http://creativecommons.org/publicdomain/zero/1.0/",
        ),
        ("public-domain", "Public domain", None),
        ("open-government", "Open Government Licence", None),
        (
            "https://example.com/my-licence",
            "https://example.com/my-licence",
            "https://example.com/my-licence",
        ),
        ("something-else", "something-else", None),
    ],
)
def test_license_label(code, label, href):
    assert license_label(code) == (label, href)


def test_tags_page_is_grouped_by_letter(archive):
    html = read(archive, "tags", "index.html")

    assert "<h2>A</h2>" in html
    assert "<h2>S</h2>" in html
    assert html.index("<h2>A</h2>") < html.index("<h2>S</h2>")
    assert 'class="tag"' not in html
