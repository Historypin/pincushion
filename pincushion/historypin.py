from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from urllib.parse import urlsplit

import click
import requests

logger = logging.getLogger(__name__)

DEFAULT_SITE = "https://historypin.org"
EXPORT_CAP = 10000
DEFAULT_RETRY_AFTER = 60


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

    def list(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """
        Yield every item in a paginated list, following the next links.
        """
        self.last_count = None
        url: str | None = f"{self.site}/api/v1/{path}"
        while url is not None:
            page = self._get(url, params)
            # the query string of the next link already carries the params
            params = None
            if self.last_count is None:
                self.last_count = page.get("count")
            yield from page["results"]
            url = self._on_site(page["next"]) if page.get("next") else None

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
