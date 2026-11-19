from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit

import click
import requests
import tqdm

if TYPE_CHECKING:
    from pincushion.media import MediaDownloader

logger = logging.getLogger(__name__)

DEFAULT_SITE = "https://historypin.org"
EXPORT_CAP = 10000
DEFAULT_RETRY_AFTER = 60
DATA_VERSION = "1.0.0"


class HistorypinError(Exception):
    """A problem talking to the Historypin API, with a message for people."""


class Client:
    """
    A small client for the Historypin data export API (/api/v1/).
    """

    def __init__(self, api_key: str, site: str = DEFAULT_SITE, pause: float = 0.25):
        self.site = site.rstrip("/")
        self.pause = pause
        self.last_count: int | None = None
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def get(self, path: str, params: dict | None = None) -> dict:
        """
        Get a path relative to /api/v1/ and return the decoded JSON.
        """
        return self._get(f"{self.site}/api/v1/{path}", params)

    def pages(self, path: str, params: dict | None = None) -> Iterator[list[dict]]:
        """
        Yield each page of a paginated list, following the next links.
        """
        self.last_count = None
        url: str | None = f"{self.site}/api/v1/{path}"
        while url is not None:
            page = self._get(url, params)
            # the query string of the next link already carries the params
            params = None
            if self.last_count is None:
                self.last_count = page.get("count")
            yield page["results"]
            url = self._on_site(page["next"]) if page.get("next") else None

    def list(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """
        Yield every item in a paginated list.
        """
        for results in self.pages(path, params):
            yield from results

    def is_capped(self) -> bool:
        """
        Whether the last list reached the API's export cap, so items may be
        missing from the end of it.
        """
        return self.last_count == EXPORT_CAP

    def _on_site(self, url: str) -> str:
        # A proxy can make the API build next links with an internal host or
        # http scheme, so request the same path on the configured site.
        parts = urlsplit(url)
        query = f"?{parts.query}" if parts.query else ""
        return f"{self.site}{parts.path}{query}"

    def _get(self, url: str, params: dict | None) -> dict:
        while True:
            time.sleep(self.pause)
            logger.info(f"fetching {url} {params or ''}")
            resp = self.session.get(url, params=params)
            if resp.status_code != 429:
                break
            wait = int(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
            logger.warning(f"rate limited, waiting {wait} seconds")
            click.echo(f"Rate limited, waiting {wait} seconds", err=True)
            time.sleep(wait)

        if resp.status_code == 200:
            return resp.json()
        raise HistorypinError(_error_message(resp))


def fetch_user(
    client: Client, downloader: MediaDownloader | None, user_id: int | None = None
) -> dict:
    """
    Fetch a member's profile, pins and collections. Without a user_id, fetch
    the API key owner's. Media is downloaded page by page as it arrives.
    """
    member = client.get("members/me/" if user_id is None else f"members/{user_id}/")
    data = _new_data("user", client.site)
    data["member"] = member
    if downloader is not None:
        downloader.fetch_image(member.get("image_url"), "index.jpg")

    data["pins"] = _fetch_pins(client, downloader, data, {"creator": member["id"]})
    # a regular key already only lists the collections its owner owns or
    # manages; for anyone else the API can only filter by owner
    params = {} if user_id is None else {"owner": user_id}
    data["collections"] = _fetch_collections(client, downloader, data, params)
    return data


def fetch_collection(
    client: Client, downloader: MediaDownloader | None, collection_id: int
) -> dict:
    """
    Fetch a collection, its descendant collections and all their pins,
    walking the tree breadth first. Media is downloaded as it arrives.
    """
    root = client.get(f"collections/{collection_id}/")
    data = _new_data("collection", client.site)
    data["collection"] = root
    if downloader is not None:
        downloader.fetch_image(root.get("banner_image_url"), "index.jpg")

    # collections can be nested in more than one place, and in principle
    # can form cycles, so remember which ones have been seen
    seen = {root["id"]}
    queue = [root]
    pins: dict[int, dict] = {}
    while queue:
        coll = queue.pop(0)
        for pin in _fetch_pins(client, downloader, data, {"collections": coll["id"]}):
            pins.setdefault(pin["id"], pin)
        for child in _fetch_collections(client, downloader, data, {"parent": coll["id"]}):
            if child["id"] not in seen:
                seen.add(child["id"])
                data["collections"].append(child)
                queue.append(child)
    data["pins"] = list(pins.values())

    # a regular key can't see other members' pins or collections it doesn't
    # manage, but each collection's child_ids and pin_ids list everything
    fetched = [root, *data["collections"]]
    missing_collections = {c for coll in fetched for c in coll.get("child_ids", [])} - seen
    missing_pins = {p for coll in fetched for p in coll.get("pin_ids", [])} - pins.keys()
    if missing_collections or missing_pins:
        data["partial"] = {
            "collections_missing": len(missing_collections),
            "pins_missing": len(missing_pins),
        }
        click.echo(
            f"Warning: the archive leaves out {len(missing_collections)} collections "
            f"and {len(missing_pins)} pins that this API key can't see. Archiving "
            "a whole collection needs a staff key.",
            err=True,
        )
    return data


def _new_data(kind: str, site: str) -> dict:
    return {
        "pincushion_version": DATA_VERSION,
        "site": site,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": kind,
        "member": None,
        "collection": None,
        "collections": [],
        "pins": [],
        "partial": None,
        "truncated": [],
    }


def _fetch_pins(
    client: Client, downloader: MediaDownloader | None, data: dict, params: dict
) -> list[dict]:
    pins: list[dict] = []
    for results in _pages(client, "pins/", params, data, "pins"):
        # download each page's images now, before their links can expire
        if downloader is not None:
            downloader.pin_images(results)
        pins.extend(results)
    return pins


def _fetch_collections(
    client: Client, downloader: MediaDownloader | None, data: dict, params: dict
) -> list[dict]:
    collections: list[dict] = []
    for results in _pages(client, "collections/", params, data, "collections"):
        if downloader is not None:
            for coll in results:
                downloader.fetch_image(
                    coll.get("banner_image_url"), f"collections/{coll['id']}/image.jpg"
                )
        collections.extend(results)
    return collections


def _pages(
    client: Client, path: str, params: dict, data: dict, desc: str
) -> Iterator[list[dict]]:
    """
    Yield the pages of a list with a progress bar, and record in data if
    the list reached the export cap.
    """
    bar = None
    for results in client.pages(path, params):
        if bar is None:
            bar = tqdm.tqdm(desc=f"{desc:20}", total=client.last_count, leave=True)
        yield results
        bar.update(len(results))
    if bar is not None:
        bar.close()

    if client.is_capped():
        listing = f"{path}?{urlencode(params)}" if params else path
        data["truncated"].append(listing)
        click.echo(
            f"Warning: {listing} reached the API's limit of {EXPORT_CAP:,} items, "
            "so the archive may be missing some.",
            err=True,
        )


def _error_message(resp: requests.Response) -> str:
    status = resp.status_code
    if status == 401:
        return "The API key is invalid or inactive. Check HISTORYPIN_KEY or --api-key."
    if status == 403 and "cf-mitigated" in resp.headers:
        return (
            "The request was blocked by Cloudflare's bot check before it reached "
            "Historypin. The site needs to exempt /api/v1/ from the challenge."
        )
    if status == 403:
        return (
            "API Access is not enabled for this account. Ask a Historypin admin "
            "to turn on the API Access feature for you."
        )
    if status == 404:
        return f"{resp.url} was not found, or isn't visible to this API key."
    if status == 400:
        return f"The API rejected the request: {_flatten(resp)}"
    return f"Unexpected {status} response from {resp.url}"


def _flatten(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict):
        return "; ".join(
            f"{key}: {' '.join(map(str, value)) if isinstance(value, list) else value}"
            for key, value in body.items()
        )
    return str(body)
