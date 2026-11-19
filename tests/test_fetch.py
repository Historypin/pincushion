from __future__ import annotations

import json

import responses
from responses import matchers

from pincushion.historypin import fetch_user
from tests.conftest import API, FakeDownloader, page

DATA_KEYS = {
    "pincushion_version",
    "site",
    "fetched_at",
    "type",
    "member",
    "collection",
    "collections",
    "pins",
    "partial",
    "truncated",
}

MEMBER = {"id": 42, "name": "Ada", "image_url": "https://img.test/m.jpg"}


def query(**params):
    return [matchers.query_param_matcher({k: str(v) for k, v in params.items()})]


@responses.activate
def test_fetch_user_self(client, downloader):
    pins = [{"id": 1, "type": "IMAGE"}, {"id": 2, "type": "TEXT"}]
    collections = [{"id": 3, "banner_image_url": None}]
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(f"{API}/pins/", json=page(pins), match=query(creator=42))
    responses.get(f"{API}/collections/", json=page(collections), match=query())

    data = fetch_user(client, downloader)

    assert set(data) == DATA_KEYS
    assert data["pincushion_version"] == "1.0.0"
    assert data["type"] == "user"
    assert data["site"] == "https://hp.test"
    assert data["fetched_at"].endswith("Z")
    assert data["member"] == MEMBER
    assert data["collection"] is None
    assert data["pins"] == pins
    assert data["collections"] == collections
    assert data["partial"] is None
    assert data["truncated"] == []


@responses.activate
def test_fetch_user_other_uses_owner_filter(client, downloader):
    responses.get(f"{API}/members/9/", json={"id": 9, "image_url": None})
    responses.get(f"{API}/pins/", json=page([]), match=query(creator=9))
    responses.get(f"{API}/collections/", json=page([]), match=query(owner=9))

    data = fetch_user(client, downloader, user_id=9)

    assert data["member"]["id"] == 9


@responses.activate
def test_images_downloaded_before_next_page(client):
    log: list = []
    responses.get(f"{API}/members/me/", json=MEMBER)

    def pins_page(request):
        log.append(("request", request.url))
        if "page=2" in request.url:
            return 200, {}, _json(page([{"id": 2, "type": "IMAGE"}], count=2))
        return 200, {}, _json(page([{"id": 1, "type": "IMAGE"}], next=f"{API}/pins/?creator=42&page=2", count=2))

    responses.add_callback(responses.GET, f"{API}/pins/", callback=pins_page)
    responses.get(f"{API}/collections/", json=page([]))

    fetch_user(client, FakeDownloader(log))

    page2 = next(i for i, e in enumerate(log) if e[0] == "request" and "page=2" in e[1])
    assert log.index(("images", [1])) < page2


@responses.activate
def test_member_and_banner_images_fetched(client, downloader):
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(f"{API}/pins/", json=page([]))
    responses.get(f"{API}/collections/", json=page([{"id": 3, "banner_image_url": "https://img.test/b.jpg"}]))

    fetch_user(client, downloader)

    assert ("image", "https://img.test/m.jpg", "index.jpg") in downloader.log
    assert ("image", "https://img.test/b.jpg", "collections/3/image.jpg") in downloader.log


@responses.activate
def test_truncated_recorded(client, downloader, capsys):
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(f"{API}/pins/", json=page([{"id": 1, "type": "TEXT"}], count=10000))
    responses.get(f"{API}/collections/", json=page([]))

    data = fetch_user(client, downloader)

    assert data["truncated"] == ["pins/?creator=42"]
    assert "10,000" in capsys.readouterr().err


def _json(body):
    return json.dumps(body)
