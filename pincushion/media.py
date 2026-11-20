from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
import tqdm
import yt_dlp

from pincushion.historypin import USER_AGENT, HistorypinError

if TYPE_CHECKING:
    from pincushion.historypin import Client

logger = logging.getLogger(__name__)

AUDIO_HOSTS = re.compile("soundcloud|audioboom")

# statuses an expired signed link can get (GCS answers 400 ExpiredToken)
REFUSED = {400, 401, 403}


def media_type(pin: dict) -> str:
    """
    Return image, video, audio or text for a pin.
    """
    kind = (pin.get("type") or "").lower()
    # some video pins are really from audio sites
    if kind == "video" and AUDIO_HOSTS.search(pin.get("video_url") or ""):
        return "audio"
    return kind if kind in ("image", "video", "audio") else "text"


def media_url(pin: dict) -> str | None:
    """
    Return the URL of a pin's primary media.
    """
    kind = media_type(pin)
    if kind == "image":
        return pin.get("image_url")
    if kind in ("video", "audio"):
        return pin.get("video_url") or pin.get("audio_url")
    return None


SOUNDCLOUD_TRACK = re.compile(
    r"api\.soundcloud\.com/tracks/(?:soundcloud:tracks:)?(\d+)"
)


def downloadable_url(url: str) -> str:
    """
    Return a URL yt-dlp can download. SoundCloud pins are often stored as the
    embedded player's URL, which wraps the track's URL in a query parameter.
    """
    parts = urlsplit(url)
    if parts.netloc == "w.soundcloud.com":
        wrapped = parse_qs(parts.query).get("url", [""])[0]
        if match := SOUNDCLOUD_TRACK.search(unquote(wrapped)):
            return f"https://api.soundcloud.com/tracks/{match.group(1)}"
    return url


class MediaDownloader:
    """
    Downloads images and audio/video into an archive directory.
    """

    def __init__(
        self,
        archive_dir: Path,
        site: str,
        client: Client | None = None,
        cookie_file: str | None = None,
        overwrite: bool = False,
        pause: float = 0.25,
    ):
        self.archive_dir = archive_dir
        self.site = site.rstrip("/")
        self.client = client
        self.cookie_file = cookie_file
        self.overwrite = overwrite
        self.refetched: set[int] = set()
        # a short pause between downloads, to be gentle on image hosts
        self.pause = pause

    def fetch_image(self, url: str | None, rel_path: str) -> bool:
        """
        Download an image to a path in the archive, and return whether the
        file is now there.
        """
        return self._fetch(url, rel_path) == 200

    def pin_image(self, pin: dict) -> None:
        """
        Download an image pin's image. Image links can be signed and expire,
        so if one is refused, get the pin again for a fresh link and retry once.
        """
        if media_type(pin) != "image":
            return
        rel_path = f"pins/{pin['id']}/image.jpg"
        status = self._fetch(pin.get("image_url"), rel_path)
        if status not in REFUSED or self.client is None or pin["id"] in self.refetched:
            return
        # only once per pin per run, since each re-fetch costs a rate-limited
        # API request and some refusals are from external hosts, not expiry
        self.refetched.add(pin["id"])
        logger.info(f"image link for pin {pin['id']} was refused, getting a fresh one")
        try:
            fresh = self.client.get(f"pins/{pin['id']}/")
        except HistorypinError as e:
            logger.error(f"unable to get a fresh image link for pin {pin['id']}: {e}")
            return
        if self._fetch(fresh.get("image_url"), rel_path) != 200:
            logger.error(f"unable to download the image for pin {pin['id']}")

    def pin_images(
        self, pins: list[dict], progress: Callable[[int], object] | None = None
    ) -> None:
        for pin in pins:
            self.pin_image(pin)
            if progress is not None:
                progress(1)

    def pin_av(self, pin: dict) -> None:
        """
        Download a video or audio pin's media with yt-dlp.
        """
        kind = media_type(pin)
        if kind not in ("video", "audio"):
            return
        url = pin.get("video_url") or pin.get("audio_url")
        if url:
            url = downloadable_url(url)
        if not url:
            logger.error(f"pin {pin['id']} has no media link")
            return

        path = self.archive_dir / "pins" / str(pin["id"]) / "media.%(ext)s"
        present = (
            path.with_suffix(".mp3").is_file() or path.with_suffix(".mp4").is_file()
        )
        if present and not self.overwrite:
            logger.info(f"skipping download of {url} since it is already present")
        else:
            self._download_av(url, kind, path)
        self.pin_poster(pin, url)

    def pin_poster(self, pin: dict, url: str) -> None:
        """
        Save the thumbnail the media's site provides as the pin's poster, so
        players and galleries don't show a black frame. yt-dlp can read it
        even when the media itself can't be downloaded.
        """
        rel_path = f"pins/{pin['id']}/poster.jpg"
        if (self.archive_dir / rel_path).is_file() and not self.overwrite:
            return
        opts = {"quiet": True, "logger": logger}
        if self.cookie_file:
            opts["cookiefile"] = self.cookie_file
        info: dict = {}
        with yt_dlp.YoutubeDL(params=opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False, process=False) or {}
            except yt_dlp.utils.DownloadError as e:
                logger.info(f"yt-dlp couldn't read the details of {url}: {e}")
        thumbnails = info.get("thumbnails") or []
        thumbnail = info.get("thumbnail") or (
            thumbnails[-1].get("url") if thumbnails else None
        )
        # yt-dlp stops at SoundCloud's DRM check before reading the artwork,
        # but SoundCloud's public oEmbed endpoint still provides it
        if not thumbnail and "soundcloud.com" in urlsplit(url).netloc:
            thumbnail = self._soundcloud_artwork(url)
        if thumbnail:
            self.fetch_image(thumbnail, rel_path)
        else:
            logger.error(f"no thumbnail found for {url}")

    def _soundcloud_artwork(self, url: str) -> str | None:
        try:
            resp = requests.get(
                "https://soundcloud.com/oembed",
                params={"format": "json", "url": url},
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json().get("thumbnail_url")
        except (requests.RequestException, ValueError) as e:
            logger.error(f"unable to get SoundCloud artwork for {url}: {e}")
            return None

    def _download_av(self, url: str, kind: str, path: Path) -> None:
        logger.info(f"downloading media {url} to {path}")
        opts: dict = {
            "noprogress": True,
            "quiet": True,
            "logger": logger,
            "format": "best/bestvideo+bestaudio",
            "audio_format": "mp3",
            "outtmpl": {"default": str(path)},
        }

        # convert to mp3 or mp4 if needed
        if kind == "video":
            opts["postprocessors"] = [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ]
        else:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
            ]

        # optionally use cookies to download video
        if self.cookie_file:
            opts["cookiefile"] = self.cookie_file

        with yt_dlp.YoutubeDL(params=opts) as ydl:
            try:
                ydl.extract_info(url)
            except yt_dlp.utils.DownloadError as e:
                logger.error(f"Unable to download media {url}: {e}")

    def all_pin_av(self, pins: list[dict], progress: bool = True) -> None:
        av_pins = [pin for pin in pins if media_type(pin) in ("video", "audio")]
        for pin in tqdm.tqdm(av_pins, desc=f"{'audio/video':20}", disable=not progress):
            self.pin_av(pin)

    def _fetch(self, url: str | None, rel_path: str) -> int | None:
        """
        Download url to rel_path, returning the HTTP status (200 when the
        file is already present) or None when there is no URL.
        """
        if not url:
            return None
        path = self.archive_dir / rel_path
        if path.is_file() and not self.overwrite:
            logger.info(f"skipping download of {url} since it is already present")
            return 200

        url = urljoin(self.site + "/", url)
        logger.info(f"saving {url} to {path}")
        try:
            time.sleep(self.pause)
            resp = requests.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as e:
            logger.error(f"unable to fetch {url}: {e}")
            return None
        if resp.status_code == 200:
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(resp.content)
        else:
            logger.error(f"received {resp.status_code} when fetching {url}")
        return resp.status_code
