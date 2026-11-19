from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from pincushion.archive import ArchiveGenerator, tag_filename

FIXTURE = Path(__file__).parent / "data" / "archive" / "data.json"


def make_archive(tmp_path: Path, **changes) -> Path:
    data = json.loads(FIXTURE.read_text())
    data.update(changes)
    (tmp_path / "data.json").write_text(json.dumps(data))
    image = tmp_path / "pins" / "10" / "image.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpg")
    return tmp_path


@pytest.fixture
def archive(tmp_path):
    ArchiveGenerator(make_archive(tmp_path)).generate()
    return tmp_path


def read(archive: Path, *parts: str) -> str:
    return archive.joinpath(*parts).read_text()


def test_rejects_old_archive(tmp_path):
    (tmp_path / "data.json").write_text(json.dumps({"user": {}, "pins": []}))

    with pytest.raises(ValueError, match="0.x"):
        ArchiveGenerator(tmp_path)


def test_layout(archive):
    for path in [
        "index.html",
        "collections/3/index.html",
        "collections/4/index.html",
        *[f"pins/{pin_id}/index.html" for pin_id in (10, 11, 12, 13, 14)],
        "tags/index.html",
        "map/index.html",
        "map/pins.geojson",
    ]:
        assert (archive / path).is_file(), path
    assert not (archive / "collections" / "99").exists()


def test_loose_pins_on_index(archive):
    index = read(archive, "index.html")
    gallery = index[index.index('class="gallery pins"') :]

    assert 'href="pins/13/index.html"' in gallery
    assert 'href="pins/14/index.html"' in gallery
    assert 'href="pins/10/index.html"' not in gallery


def test_missing_image_still_writes_later_pins(archive):
    # pin 13 is an image pin whose image was never downloaded
    assert (archive / "pins/13/index.html").is_file()
    assert (archive / "pins/14/index.html").is_file()


def test_map_skips_pins_without_location(archive):
    geojson = json.loads(read(archive, "map", "pins.geojson"))
    ids = [feature["properties"]["id"] for feature in geojson["features"]]

    assert 13 not in ids
    assert 10 in ids


def test_tag_files_and_links(archive):
    assert tag_filename("a/b") == "a-b"
    assert (archive / "tags" / "a-b.html").is_file()
    assert (archive / "tags" / "café.html").is_file()
    assert (archive / "tags" / f"{tag_filename('what?#')}.html").is_file()

    page = read(archive, "pins", "12", "index.html")
    assert f'href="../../tags/{quote(tag_filename("what?#"))}.html"' in page


def test_pin_page_fields(archive):
    page = read(archive, "pins", "10", "index.html")

    assert "View from Twin Peaks" in page
    assert "Ada Lovelace" in page
    assert "ID-10" in page
    assert 'href="https://hp.test/pins/10/"' in page
    assert '<a href="http://creativecommons.org/licenses/by/3.0/">' in page


def test_text_pin_shows_description(archive):
    page = read(archive, "pins", "12", "index.html")
    media = page[page.index('class="media"') : page.index("</section>")]

    assert "In the summer of 1925 downtown was bustling." in media


def test_partial_note(tmp_path):
    archive = make_archive(tmp_path, partial={"collections_missing": 1, "pins_missing": 2})

    ArchiveGenerator(archive).generate()

    assert "leaves out" in read(archive, "index.html")


def test_empty_archive(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data.update(collections=[], pins=[])
    (tmp_path / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(tmp_path).generate()

    assert json.loads(read(tmp_path, "map", "pins.geojson"))["features"] == []
    assert "if (pins.features.length)" in read(tmp_path, "map", "index.html")


def test_collection_archive_index(tmp_path):
    data = json.loads(FIXTURE.read_text())
    root = {**data["collections"][0], "id": 1, "title": "Root collection"}
    data.update(type="collection", member=None, collection=root)
    (tmp_path / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(tmp_path).generate()

    assert "Root collection" in read(tmp_path, "index.html")

