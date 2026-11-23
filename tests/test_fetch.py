from __future__ import annotations

import json

import responses
from responses import matchers

from pincushion import historypin
from pincushion.historypin import fetch_collection, fetch_user
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
    pins = [
        {"id": 1, "type": "IMAGE", "collection_ids": [3]},
        {"id": 2, "type": "TEXT", "collection_ids": []},
    ]
    collections = [{"id": 3, "banner_image_url": None}]
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(f"{API}/pins/", json=page(pins), match=query(creator=42))
    responses.get(f"{API}/collections/", json=page(collections), match=query())

    data = fetch_user(client, downloader)

    assert set(data) == DATA_KEYS
    assert data["pincushion_version"] == "0.6.0"
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
        return (
            200,
            {},
            _json(
                page(
                    [{"id": 1, "type": "IMAGE"}],
                    next=f"{API}/pins/?creator=42&page=2",
                    count=2,
                )
            ),
        )

    responses.add_callback(responses.GET, f"{API}/pins/", callback=pins_page)
    responses.get(f"{API}/collections/", json=page([]))

    fetch_user(client, FakeDownloader(log))

    page2 = next(i for i, e in enumerate(log) if e[0] == "request" and "page=2" in e[1])
    assert log.index(("images", [1])) < page2


@responses.activate
def test_member_and_banner_images_fetched(client, downloader):
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(
        f"{API}/pins/", json=page([{"id": 1, "type": "TEXT", "collection_ids": [3]}])
    )
    responses.get(
        f"{API}/collections/",
        json=page([{"id": 3, "banner_image_url": "https://img.test/b.jpg"}]),
    )

    fetch_user(client, downloader)

    assert ("image", "https://img.test/m.jpg", "index.jpg") in downloader.log
    assert (
        "image",
        "https://img.test/b.jpg",
        "collections/3/image.jpg",
    ) in downloader.log


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


def coll(id, child_ids=(), pin_ids=(), banner=None):
    return {
        "id": id,
        "child_ids": list(child_ids),
        "pin_ids": list(pin_ids),
        "banner_image_url": banner,
    }


def pin(id):
    return {"id": id, "type": "TEXT"}


def serve_tree(collections: dict, children: dict, pins: dict):
    """Register the detail, ?parent= and ?collections= endpoints for a tree."""
    for cid, record in collections.items():
        responses.get(f"{API}/collections/{cid}/", json=record)
        responses.get(
            f"{API}/collections/",
            json=page([collections[c] for c in children.get(cid, [])]),
            match=query(parent=cid),
        )
        responses.get(
            f"{API}/pins/",
            json=page([pin(p) for p in pins.get(cid, [])]),
            match=query(collections=cid),
        )


@responses.activate
def test_walks_tree_breadth_first_with_dedupe(client, downloader):
    collections = {
        1: coll(1, [2, 3], [10]),
        2: coll(2, [4], [10]),
        3: coll(3, [4], [11]),
        4: coll(4, [], [12]),
    }
    serve_tree(
        collections, {1: [2, 3], 2: [4], 3: [4]}, {1: [10], 2: [10], 3: [11], 4: [12]}
    )

    data = fetch_collection(client, downloader, 1)

    assert data["type"] == "collection"
    assert data["member"] is None
    assert data["collection"] == collections[1]
    assert [c["id"] for c in data["collections"]] == [2, 3, 4]
    assert [p["id"] for p in data["pins"]] == [10, 11, 12]
    assert data["partial"] is None


@responses.activate
def test_cycle_terminates(client, downloader):
    collections = {1: coll(1, [2]), 2: coll(2, [1])}
    serve_tree(collections, {1: [2], 2: [1]}, {})

    data = fetch_collection(client, downloader, 1)

    assert [c["id"] for c in data["collections"]] == [2]
    assert data["partial"] is None


@responses.activate
def test_partial_counts(client, downloader, capsys):
    collections = {1: coll(1, [2, 5], [10, 11, 12]), 2: coll(2)}
    serve_tree(collections, {1: [2]}, {1: [10, 11]})

    data = fetch_collection(client, downloader, 1)

    assert data["partial"] == {"collections_missing": 1, "pins_missing": 1}
    assert "leaves out" in capsys.readouterr().err


@responses.activate
def test_root_banner_is_index_image(client, downloader):
    serve_tree({1: coll(1, banner="https://img.test/root.jpg")}, {}, {})

    fetch_collection(client, downloader, 1)

    assert ("image", "https://img.test/root.jpg", "index.jpg") in downloader.log


@responses.activate
def test_own_archive_keeps_only_collections_with_its_pins(client, downloader):
    # a staff key's unfiltered /collections/ lists every collection on the site
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(
        f"{API}/pins/", json=page([{"id": 1, "type": "TEXT", "collection_ids": [3]}])
    )
    responses.get(
        f"{API}/collections/",
        json=page(
            [
                {"id": 3, "banner_image_url": "https://img.test/3.jpg"},
                {"id": 5, "banner_image_url": "https://img.test/5.jpg"},
            ]
        ),
    )

    data = fetch_user(client, downloader)

    assert [c["id"] for c in data["collections"]] == [3]
    assert (
        "image",
        "https://img.test/5.jpg",
        "collections/5/image.jpg",
    ) not in downloader.log


@responses.activate
def test_pin_progress_advances_per_pin(client, monkeypatch):
    updates = []

    class FakeBar:
        def __init__(self, *args, desc="", **kwargs):
            self.desc = desc.strip()

        def update(self, n):
            if self.desc == "pins":
                updates.append(n)

        def close(self):
            pass

    class CountingDownloader(FakeDownloader):
        def pin_images(self, pins, progress=None):
            for _ in pins:
                progress(1)

    monkeypatch.setattr(historypin.tqdm, "tqdm", FakeBar)
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(
        f"{API}/pins/",
        json=page([{"id": n, "type": "TEXT"} for n in (1, 2, 3)]),
    )
    responses.get(f"{API}/collections/", json=page([]))

    fetch_user(client, CountingDownloader())

    assert updates == [1, 1, 1]


@responses.activate
def test_fetch_user_says_whose_archive_it_is(client, downloader, capsys):
    responses.get(
        f"{API}/members/9/", json={"id": 9, "name": "Grace Hopper", "image_url": None}
    )
    responses.get(f"{API}/pins/", json=page([]))
    responses.get(f"{API}/collections/", json=page([]))

    fetch_user(client, downloader, user_id=9)

    assert "Archiving Grace Hopper (user ID 9)" in capsys.readouterr().out


@responses.activate
def test_staff_own_archive_gets_collections_one_by_one(client, downloader):
    # a staff key's unfiltered list is the whole site, here 50 pages of it,
    # so getting the two wanted collections directly takes fewer requests
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(
        f"{API}/pins/",
        json=page([{"id": 1, "type": "TEXT", "collection_ids": [700, 3]}]),
    )
    listing = responses.get(
        f"{API}/collections/",
        json=page(
            [{"id": n, "banner_image_url": None} for n in range(100, 200)],
            next=f"{API}/collections/?page=2",
            count=5000,
        ),
    )
    responses.get(f"{API}/collections/3/", json={"id": 3, "banner_image_url": None})
    responses.get(f"{API}/collections/700/", json={"id": 700, "banner_image_url": None})

    data = fetch_user(client, downloader)

    assert listing.call_count == 1
    assert [c["id"] for c in data["collections"]] == [3, 700]
    assert data["truncated"] == []


@responses.activate
def test_own_archive_leaves_out_collections_the_key_cannot_see(client, downloader):
    # a regular key's whole list is short, and lacks collection 5
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(
        f"{API}/pins/",
        json=page([{"id": 1, "type": "TEXT", "collection_ids": [3, 5]}]),
    )
    responses.get(
        f"{API}/collections/", json=page([{"id": 3, "banner_image_url": None}])
    )

    data = fetch_user(client, downloader)

    assert [c["id"] for c in data["collections"]] == [3]
    assert len(responses.calls) == 3


@responses.activate
def test_own_archive_without_collections_lists_none(client, downloader):
    responses.get(f"{API}/members/me/", json=MEMBER)
    responses.get(f"{API}/pins/", json=page([{"id": 1, "type": "TEXT"}]))

    data = fetch_user(client, downloader)

    assert data["collections"] == []
    assert len(responses.calls) == 2


@responses.activate
def test_empty_lists_are_not_requested(client, downloader):
    collections = {1: coll(1, [2], [10]), 2: coll(2)}
    serve_tree(collections, {1: [2]}, {1: [10]})

    fetch_collection(client, downloader, 1)

    requested = [call.request.url for call in responses.calls]
    assert not any("parent=2" in url or "collections=2" in url for url in requested)
