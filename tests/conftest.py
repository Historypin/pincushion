from __future__ import annotations

import pytest

from pincushion.historypin import Client

SITE = "https://hp.test"
API = f"{SITE}/api/v1"


def page(
    results: list[dict], next: str | None = None, count: int | None = None
) -> dict:
    """Build the body of one page of an /api/v1/ list."""
    return {
        "count": len(results) if count is None else count,
        "next": next,
        "previous": None,
        "results": results,
    }


class FakeDownloader:
    """Records what the fetch functions ask to download, in order, in `log`."""

    def __init__(self, log: list | None = None):
        self.log = [] if log is None else log

    def fetch_image(self, url, rel_path):
        self.log.append(("image", url, rel_path))
        return True

    def pin_images(self, pins, progress=None):
        self.log.append(("images", [pin["id"] for pin in pins]))


@pytest.fixture(autouse=True)
def no_media_pause(monkeypatch):
    """Skip MediaDownloader's pause between downloads, except where a test
    patches sleep itself."""
    from pincushion import media

    monkeypatch.setattr(media.time, "sleep", lambda seconds: None)


@pytest.fixture
def client():
    return Client("k", site=SITE, pause=0)


@pytest.fixture
def downloader():
    return FakeDownloader()
