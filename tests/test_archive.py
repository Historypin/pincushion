from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from pincushion.archive import ArchiveGenerator, tag_filename, tag_files

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

    with pytest.raises(ValueError, match="0.5 or earlier"):
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
    description = page[page.index('class="prose description"') :]

    assert "In the summer of 1925 downtown was bustling." in description


def test_partial_note(tmp_path):
    archive = make_archive(
        tmp_path, partial={"collections_missing": 1, "pins_missing": 2}
    )

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


def test_colliding_tags_get_distinct_files(tmp_path):
    archive = make_archive(tmp_path)
    data = json.loads((archive / "data.json").read_text())
    tags = ["Paris", "paris", "a/b", "a-b", "index"]
    for pin, tag in zip(data["pins"], tags):
        pin["tags"] = [tag]
    (archive / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(archive).generate()

    files = tag_files(tags)
    assert len({name.lower() for name in files.values()}) == len(tags)
    assert "index" not in {name.lower() for name in files.values()}
    for pin, tag in zip(data["pins"], tags):
        page = read(archive, "pins", str(pin["id"]), "index.html")
        assert f'href="../../tags/{quote(files[tag])}.html"' in page
        assert tag in read(archive, "tags", f"{files[tag]}.html")
    assert "Pins that are tagged" not in read(archive, "tags", "index.html")


def test_null_fields_do_not_render_none(tmp_path):
    archive = make_archive(tmp_path)
    data = json.loads((archive / "data.json").read_text())
    data["member"]["name"] = None
    data["member"]["about"] = None
    data["pins"][2]["description"] = None
    data["collections"][1]["long_description"] = None
    data["collections"][1]["short_description"] = None
    (archive / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(archive).generate()

    for page in ["index.html", "pins/12/index.html", "collections/4/index.html"]:
        assert ">None<" not in read(archive, page), page
        assert "None</" not in read(archive, page), page


def test_map_credits_openstreetmap(archive):
    page = read(archive, "map", "index.html")

    assert "OpenStreetMap</a> contributors" in page


def test_map_data_cannot_close_its_script_tag(tmp_path):
    archive = make_archive(tmp_path)
    data = json.loads((archive / "data.json").read_text())
    data["pins"][0]["title"] = "Smith & Sons </script><b>"
    (archive / "data.json").write_text(json.dumps(data))

    ArchiveGenerator(archive).generate()

    page = read(archive, "map", "index.html")
    assert "</script><b>" not in page
    assert "\\u003c/script>" in page


def test_map_has_an_offline_basemap(archive):
    basemap = read(archive, "map", "basemap.js")
    page = read(archive, "map", "index.html")

    assert basemap.startswith("// Coastlines")
    assert "window.PINCUSHION_BASEMAP" in basemap
    assert '<script src="basemap.js"></script>' in page
    assert "window.PINCUSHION_BASEMAP" in page
    assert "Natural Earth" in page


def test_map_sets_its_own_max_zoom(archive):
    # the cluster plugin needs one, and offline there's no tile layer to give it
    assert "maxZoom: 18" in read(archive, "map", "index.html")


def test_media_that_failed_to_download_links_to_the_original(archive):
    # pin 11's video and pin 14's audio were never downloaded
    video = read(archive, "pins", "11", "index.html")
    audio = read(archive, "pins", "14", "index.html")

    assert "<video" not in video
    assert "couldn't be downloaded" in video
    assert 'href="https://www.youtube.com/watch?v=x"' in video
    assert "<audio" not in audio
    assert 'href="https://soundcloud.com/a/b"' in audio


def test_video_and_audio_posters_are_shown(tmp_path):
    archive = make_archive(tmp_path)
    for pin_id, name in ((11, "media.mp4"), (14, "media.mp3")):
        (archive / "pins" / str(pin_id)).mkdir(parents=True, exist_ok=True)
        (archive / "pins" / str(pin_id) / name).write_bytes(b"media")
        (archive / "pins" / str(pin_id) / "poster.jpg").write_bytes(b"jpg")

    ArchiveGenerator(archive).generate()

    assert 'poster="poster.jpg"' in read(archive, "pins", "11", "index.html")
    assert '<img src="poster.jpg"' in read(archive, "pins", "14", "index.html")
    collection = read(archive, "collections", "3", "index.html")
    assert 'src="../../pins/11/poster.jpg"' in collection


@pytest.mark.parametrize(
    "tag, name",
    [
        ("WW2: Home Front", "WW2- Home Front"),
        ('a<b>c"d\\e|f?g*h', "a-b-c-d-e-f-g-h"),
        ("tab\there", "tab-here"),
        ("trailing dots...", "trailing dots"),
        ("...", "tag"),
    ],
)
def test_tag_filename_is_safe_everywhere(tag, name):
    assert tag_filename(tag) == name


def test_long_tags_get_short_distinct_filenames():
    first, second = "é" * 200 + "1", "é" * 200 + "2"

    files = tag_files([first, second])

    assert all(len(name.encode()) <= 110 for name in files.values())
    assert files[first] != files[second]
    assert tag_filename(first) == "é" * 50
