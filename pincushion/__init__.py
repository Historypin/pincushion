from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Callable

import click

from pincushion import historypin
from pincushion.archive import ArchiveGenerator
from pincushion.historypin import DEFAULT_SITE, Client, HistorypinError
from pincushion.media import MediaDownloader


@click.group()
def cli():
    """
    Create an archive for Historypin resources.
    """
    logging.basicConfig(filename="pincushion.log", level=logging.INFO)


def api_options(func: Callable) -> Callable:
    """
    Add the --api-key and --site options, which fall back to the
    HISTORYPIN_KEY and HISTORYPIN_SITE environment variables.
    """
    func = click.option(
        "--site",
        envvar="HISTORYPIN_SITE",
        help="The Historypin site to use (default: HISTORYPIN_SITE or historypin.org)",
    )(func)
    func = click.option(
        "--api-key",
        envvar="HISTORYPIN_KEY",
        help="A Historypin API key (default: the HISTORYPIN_KEY environment variable)",
    )(func)
    return func


def cookie_option(func: Callable) -> Callable:
    return click.option(
        "--cookie-file",
        help="use a cookie file to download video, see https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp",
        type=click.Path(exists=True, dir_okay=False),
    )(func)


def archive_path_option(func: Callable) -> Callable:
    return click.option(
        "--archive-path",
        help="Where to write the archive files",
        type=click.Path(),
        default="archive",
    )(func)


def api_errors(func: Callable) -> Callable:
    """
    Show HistorypinError messages without a traceback.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (HistorypinError, ValueError) as e:
            raise click.ClickException(str(e)) from e

    return wrapper


def make_client(api_key: str | None, site: str | None) -> Client:
    site = site or DEFAULT_SITE
    if not api_key:
        raise click.UsageError(
            "An API key is needed. Set HISTORYPIN_KEY or pass --api-key. "
            f"You can make one at {site.rstrip('/')}/api-keys/"
        )
    return Client(api_key, site=site)


def build_archive(
    archive_path: str,
    cookie_file: str | None,
    client: Client,
    fetch: Callable[[MediaDownloader], dict],
) -> None:
    archive_dir = Path(archive_path)
    archive_dir.mkdir(parents=True, exist_ok=True)
    downloader = MediaDownloader(archive_dir, client.site, client=client, cookie_file=cookie_file)

    data = fetch(downloader)
    # write the data first so regenerate can pick up if media fails
    with (archive_dir / "data.json").open("w") as fh:
        json.dump(data, fh, indent=2)

    ArchiveGenerator(archive_dir, downloader=downloader).generate()


@cli.command("user")
@click.option("--user-id", help="A Historypin user ID (default: the API key's owner)", type=int)
@archive_path_option
@cookie_option
@api_options
@api_errors
def user(
    user_id: int | None,
    archive_path: str,
    cookie_file: str | None,
    api_key: str | None,
    site: str | None,
):
    """
    Create an archive for a Historypin member: your own by default, or
    another member's with a staff API key. This is probably the command you
    will want to be using.
    """
    client = make_client(api_key, site)
    click.echo(f"Generating archive for user-id: {user_id or 'me'}")
    build_archive(
        archive_path,
        cookie_file,
        client,
        lambda downloader: historypin.fetch_user(client, downloader, user_id=user_id),
    )


@cli.command("collection")
@click.option("--collection-id", help="A Historypin collection ID", type=int, required=True)
@archive_path_option
@cookie_option
@api_options
@api_errors
def collection(
    collection_id: int,
    archive_path: str,
    cookie_file: str | None,
    api_key: str | None,
    site: str | None,
):
    """
    Create an archive for a Historypin collection and its sub-collections.
    Including other members' pins needs a staff API key.
    """
    client = make_client(api_key, site)
    click.echo(f"Generating archive for collection: {collection_id}")
    build_archive(
        archive_path,
        cookie_file,
        client,
        lambda downloader: historypin.fetch_collection(client, downloader, collection_id),
    )


def existing_archive(
    archive_path: str, cookie_file: str | None, api_key: str | None, site: str | None
) -> ArchiveGenerator:
    """
    An ArchiveGenerator for an existing archive. With an API key it can
    re-fetch expired image links; without one it skips them.
    """
    archive_dir = Path(archive_path)
    data = json.loads((archive_dir / "data.json").read_text())
    site = site or data.get("site") or DEFAULT_SITE
    client = Client(api_key, site=site) if api_key else None
    downloader = MediaDownloader(archive_dir, site, client=client, cookie_file=cookie_file)
    return ArchiveGenerator(archive_dir, downloader=downloader)


@cli.command()
@click.option("--archive-path", required=True, type=click.Path(file_okay=False, exists=True))
@cookie_option
@api_options
@api_errors
def regenerate(archive_path: str, cookie_file: str | None, api_key: str | None, site: str | None):
    """
    Regenerate the archive using a directory containing a data.json file. This
    can be useful if improvements are made to the static site generation, and
    you don't want to have to refetch all the data from Historypin.
    """
    existing_archive(archive_path, cookie_file, api_key, site).generate()


@cli.command("user-data")
@click.option("--user-id", help="A Historypin user ID (default: the API key's owner)", type=int)
@click.option("--output", help="Where to write the data", type=click.File("w"), required=True)
@api_options
@api_errors
def user_data(user_id: int | None, output, api_key: str | None, site: str | None):
    """
    Download the JSON metadata for a Historypin member.
    """
    data = historypin.fetch_user(make_client(api_key, site), None, user_id=user_id)
    output.write(json.dumps(data, indent=2))


@cli.command("collection-data")
@click.option("--collection-id", help="A Historypin collection ID", type=int, required=True)
@click.option("--output", help="Where to write the data", type=click.File("w"), required=True)
@api_options
@api_errors
def collection_data(collection_id: int, output, api_key: str | None, site: str | None):
    """
    Download the JSON metadata for a Historypin collection. Note the collection
    may include sub-collections.
    """
    data = historypin.fetch_collection(make_client(api_key, site), None, collection_id)
    output.write(json.dumps(data, indent=2))


@cli.command("media")
@click.option("--archive-path", required=True, type=click.Path(file_okay=False, exists=True))
@cookie_option
@api_options
@api_errors
def media_command(
    archive_path: str, cookie_file: str | None, api_key: str | None, site: str | None
):
    """
    Download the media for a given archive. This can be useful for testing.
    """
    existing_archive(archive_path, cookie_file, api_key, site).download_media()


if __name__ == "__main__":
    cli()
