import logging

from pincushion import historypin

logging.basicConfig(filename="test.log", level=logging.INFO)


def test_get_user():
    data = historypin.get_user(user_id=120327)

    assert "user" in data

    assert "collections" in data
    assert len(data["collections"]) > 0

    assert "tours" in data
    assert len(data["tours"]) > 0

    assert "pins" in data
    assert len(data["pins"]) > 0


def test_get_collection():
    data = historypin.get_collection(
        slug="sanborn-maps-of-america/new-orleans-sanborn-maps"
    )
    assert data["counts"]["pins"] > 0
    assert len(data["collections"]) > 0
    assert len(data["pins"]) > 0


def test_get_gallery():
    data = list(historypin.get_gallery(slug="2025-summer-institute"))
    assert len(data) > 0


def test_unique_collections():
    coll = {
        "id": 1,
        "slug": "a",
        "collections": [
            {
                "id": 2,
                "slug": "a/b",
                "collections": [{"id": 3, "slug": "a/b/c", "collections": []}],
            },
            {
                "id": 4,
                "slug": "a/c",
                "collections": [{"id": 5, "slug": "a/c/d", "collections": []}],
            },
            # this repeated sub-collection should be deduped
            {"id": 3, "slug": "a/b/c", "collections": []},
        ],
    }

    collections = list(historypin._flatten_collections([coll]))
    assert len(collections) == 6

    unique_collections = list(historypin._unique_collections([coll]))
    assert len(unique_collections) == 5

    slugs = set([coll["slug"] for coll in unique_collections])
    assert len(slugs) == 5
    assert "a" in slugs
    assert "a/b" in slugs
    assert "a/b/c" in slugs
    assert "a/c" in slugs
    assert "a/c/d" in slugs
