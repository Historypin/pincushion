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
    data = historypin.get_collection(slug="2025-summer-institute")

    assert data["counts"]["pins"] > 0
    assert len(data["collections"]) > 0
    assert len(data["pins"]) > 0

    import json; json.dump(data, open('coll.json', 'w'), indent=2)


def test_get_gallery():
    data = list(historypin.get_gallery(slug="2025-summer-institute"))
    assert len(data) > 0

