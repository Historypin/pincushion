from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import pincushion
from pincushion import historypin
from pincushion.historypin import HistorypinError


def minimal_data(site="https://historypin.org"):
    return {
        "pincushion_version": "1.0.0",
        "site": site,
        "fetched_at": "2026-11-19T20:00:00Z",
        "type": "user",
        "member": {"id": 42, "name": "Ada", "about": "", "image_url": None},
        "collection": None,
        "collections": [],
        "pins": [],
        "partial": None,
        "truncated": [],
    }


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv("HISTORYPIN_KEY", raising=False)
    monkeypatch.delenv("HISTORYPIN_SITE", raising=False)


@pytest.fixture
def seen(monkeypatch):
    calls = {}

    def fake_fetch_user(client, downloader, user_id=None):
        calls["client"] = client
        calls["user_id"] = user_id
        return minimal_data(client.site)

    monkeypatch.setattr(historypin, "fetch_user", fake_fetch_user)
    return calls


def test_missing_key(tmp_path):
    result = CliRunner().invoke(
        pincushion.cli, ["user", "--archive-path", str(tmp_path)]
    )

    assert result.exit_code != 0
    assert "HISTORYPIN_KEY" in result.output
    assert "/api-keys/" in result.output


def test_key_from_env(tmp_path, seen):
    result = CliRunner().invoke(
        pincushion.cli,
        ["user", "--archive-path", str(tmp_path)],
        env={"HISTORYPIN_KEY": "k"},
    )

    assert result.exit_code == 0, result.output
    assert seen["client"].session.headers["Authorization"] == "Bearer k"


def test_site_option(tmp_path, seen):
    result = CliRunner().invoke(
        pincushion.cli,
        [
            "user",
            "--api-key",
            "k",
            "--site",
            "http://localhost:8000",
            "--archive-path",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["client"].site == "http://localhost:8000"


def test_historypin_error_exits_nonzero(tmp_path, monkeypatch):
    def fail(client, downloader, user_id=None):
        raise HistorypinError("API Access is not enabled")

    monkeypatch.setattr(historypin, "fetch_user", fail)

    result = CliRunner().invoke(
        pincushion.cli, ["user", "--api-key", "k", "--archive-path", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "API Access is not enabled" in result.output
    assert "Traceback" not in result.output


def test_user_writes_data_and_site(tmp_path, seen):
    result = CliRunner().invoke(
        pincushion.cli,
        ["user", "--api-key", "k", "--user-id", "42", "--archive-path", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert seen["user_id"] == 42
    assert (
        json.loads((tmp_path / "data.json").read_text())["pincushion_version"]
        == "1.0.0"
    )
    assert (tmp_path / "index.html").is_file()


def test_collection_requires_id(tmp_path):
    result = CliRunner().invoke(
        pincushion.cli,
        ["collection", "--api-key", "k", "--archive-path", str(tmp_path)],
    )

    assert result.exit_code == 2
