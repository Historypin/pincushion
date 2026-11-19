from __future__ import annotations

import pytest
import requests
import responses

from pincushion import historypin
from pincushion.historypin import Client, HistorypinError

API = "https://hp.test/api/v1"


@pytest.fixture
def client():
    return Client("k", site="https://hp.test", pause=0)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(historypin.time, "sleep", calls.append)
    return calls


@responses.activate
def test_get_sends_bearer_key(client):
    responses.get(f"{API}/members/me/", json={"id": 42})

    assert client.get("members/me/") == {"id": 42}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer k"
    assert responses.calls[0].request.url == f"{API}/members/me/"


@responses.activate
def test_list_follows_next(client):
    responses.get(
        f"{API}/pins/?page=2",
        json={"count": 3, "next": None, "results": [{"id": 3}]},
    )
    responses.get(
        f"{API}/pins/",
        json={
            "count": 3,
            "next": f"{API}/pins/?page=2",
            "results": [{"id": 1}, {"id": 2}],
        },
    )

    assert [pin["id"] for pin in client.list("pins/")] == [1, 2, 3]
    assert client.last_count == 3


@responses.activate
def test_list_rewrites_next_onto_site(client):
    responses.get(
        f"{API}/pins/",
        json={
            "count": 2,
            "next": "http://internal:8000/api/v1/pins/?page=2",
            "results": [{"id": 1}],
        },
    )
    responses.get(
        f"{API}/pins/?page=2", json={"count": 2, "next": None, "results": [{"id": 2}]}
    )

    assert [pin["id"] for pin in client.list("pins/")] == [1, 2]
    assert responses.calls[1].request.url == f"{API}/pins/?page=2"


@responses.activate
@pytest.mark.parametrize("count, capped", [(10000, True), (3, False)])
def test_is_capped(client, count, capped):
    responses.get(f"{API}/pins/", json={"count": count, "next": None, "results": []})

    list(client.list("pins/"))

    assert client.is_capped() is capped


@responses.activate
@pytest.mark.parametrize(
    "status, headers, body, phrase",
    [
        (401, {}, {"detail": "Invalid or inactive API key"}, "invalid or inactive"),
        (403, {"cf-mitigated": "challenge"}, None, "Cloudflare"),
        (403, {}, {"detail": "no"}, "API Access"),
        (404, {}, {"detail": "Not found."}, "not found"),
        (
            400,
            {},
            {"collections": ["Select a valid choice."]},
            "Select a valid choice.",
        ),
    ],
)
def test_error_messages(client, status, headers, body, phrase):
    responses.get(f"{API}/pins/", status=status, headers=headers, json=body)

    with pytest.raises(HistorypinError) as e:
        client.get("pins/")

    assert phrase in str(e.value)


@responses.activate
def test_429_waits_then_retries(client, sleeps):
    responses.get(f"{API}/pins/", status=429, headers={"Retry-After": "7"})
    responses.get(f"{API}/pins/", json={"ok": True})

    assert client.get("pins/") == {"ok": True}
    assert 7 in sleeps


@responses.activate
def test_429_without_retry_after_waits_60(client, sleeps):
    responses.get(f"{API}/pins/", status=429)
    responses.get(f"{API}/pins/", json={"ok": True})

    assert client.get("pins/") == {"ok": True}
    assert 60 in sleeps


@responses.activate
def test_server_error_is_retried(client, sleeps):
    responses.get(f"{API}/pins/", status=502)
    responses.get(f"{API}/pins/", json={"ok": True})

    assert client.get("pins/") == {"ok": True}


@responses.activate
def test_connection_error_retried_then_reported(client, sleeps):
    responses.get(f"{API}/pins/", body=requests.ConnectionError("down"))

    with pytest.raises(HistorypinError) as e:
        client.get("pins/")

    assert "Couldn't reach" in str(e.value)
    assert len(responses.calls) == 3


@responses.activate
def test_requests_have_a_timeout(client, monkeypatch):
    seen = {}
    original = client.session.get

    def get(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.session, "get", get)
    responses.get(f"{API}/pins/", json={})

    client.get("pins/")

    assert seen.get("timeout")
