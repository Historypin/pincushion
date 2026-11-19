from __future__ import annotations

import logging

import pytest
import responses

from pincushion import media
from pincushion.historypin import Client
from pincushion.media import MediaDownloader, media_type

SITE = "https://hp.test"


@pytest.fixture
def client():
    return Client("k", site=SITE, pause=0)


@pytest.mark.parametrize(
    "pin, expected",
    [
        ({"type": "IMAGE"}, "image"),
        ({"type": "TEXT"}, "text"),
        ({"type": "AUDIO", "audio_url": "https://example.com/a.mp3"}, "audio"),
        ({"type": "VIDEO", "video_url": "https://youtube.com/watch?v=x"}, "video"),
        ({"type": "VIDEO", "video_url": "https://soundcloud.com/a/b"}, "audio"),
    ],
)
def test_media_type(pin, expected):
    assert media_type(pin) == expected


@responses.activate
def test_fetch_image_absolute_and_relative(tmp_path):
    responses.get("https://cdn.test/a.jpg", body=b"abs")
    responses.get(f"{SITE}/media/x.jpg", body=b"rel")
    downloader = MediaDownloader(tmp_path, SITE)

    assert downloader.fetch_image("https://cdn.test/a.jpg", "pins/1/image.jpg")
    assert downloader.fetch_image("/media/x.jpg", "pins/2/image.jpg")

    assert (tmp_path / "pins/1/image.jpg").read_bytes() == b"abs"
    assert (tmp_path / "pins/2/image.jpg").read_bytes() == b"rel"


@responses.activate
def test_fetch_image_skips_existing(tmp_path):
    path = tmp_path / "pins/1/image.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")

    assert MediaDownloader(tmp_path, SITE).fetch_image(
        "https://cdn.test/a.jpg", "pins/1/image.jpg"
    )

    assert len(responses.calls) == 0
    assert path.read_bytes() == b"old"


@responses.activate
def test_pin_image_refetches_expired_link(tmp_path, client):
    responses.get("https://gcs.test/old.jpg", status=403)
    responses.get(
        f"{SITE}/api/v1/pins/5/",
        json={"id": 5, "image_url": "https://gcs.test/new.jpg"},
    )
    responses.get("https://gcs.test/new.jpg", body=b"fresh")
    downloader = MediaDownloader(tmp_path, SITE, client=client)

    downloader.pin_image(
        {"id": 5, "type": "IMAGE", "image_url": "https://gcs.test/old.jpg"}
    )

    assert (tmp_path / "pins/5/image.jpg").read_bytes() == b"fresh"
    pin_calls = [c for c in responses.calls if c.request.url.endswith("/pins/5/")]
    assert len(pin_calls) == 1


@responses.activate
def test_pin_image_gives_up_after_one_refetch(tmp_path, client, caplog):
    responses.get("https://gcs.test/old.jpg", status=403)
    responses.get(
        f"{SITE}/api/v1/pins/5/",
        json={"id": 5, "image_url": "https://gcs.test/new.jpg"},
    )
    responses.get("https://gcs.test/new.jpg", status=403)
    downloader = MediaDownloader(tmp_path, SITE, client=client)

    with caplog.at_level(logging.ERROR):
        downloader.pin_image(
            {"id": 5, "type": "IMAGE", "image_url": "https://gcs.test/old.jpg"}
        )

    assert not (tmp_path / "pins/5/image.jpg").exists()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@responses.activate
def test_pin_image_without_client_skips_refetch(tmp_path):
    responses.get("https://gcs.test/old.jpg", status=403)

    MediaDownloader(tmp_path, SITE).pin_image(
        {"id": 5, "type": "IMAGE", "image_url": "https://gcs.test/old.jpg"}
    )

    assert [c.request.url for c in responses.calls] == ["https://gcs.test/old.jpg"]


def test_pin_av_calls_ytdlp(tmp_path, monkeypatch):
    seen = {}

    class FakeYoutubeDL:
        def __init__(self, params):
            seen["params"] = params

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url):
            seen["url"] = url

    monkeypatch.setattr(media.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    MediaDownloader(tmp_path, SITE).pin_av(
        {"id": 7, "type": "VIDEO", "video_url": "https://youtube.com/watch?v=x"}
    )

    assert seen["url"] == "https://youtube.com/watch?v=x"
    assert seen["params"]["outtmpl"]["default"].endswith("pins/7/media.%(ext)s")
    keys = [p["key"] for p in seen["params"]["postprocessors"]]
    assert "FFmpegVideoConvertor" in keys
