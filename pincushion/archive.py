from __future__ import annotations

import functools
import json
import logging
import re
import shutil
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from shutil import copytree, rmtree
from urllib.parse import urlsplit

import jinja2
import tqdm

from pincushion.media import MediaDownloader, media_type, media_url

logger = logging.getLogger(__name__)


# characters some filesystems don't allow in names, including control
# characters (yes, we've noticed some nulls in tags)
UNSAFE_CHARACTERS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
# well under the usual 255 byte limit, leaving room for a suffix and .html
MAX_TAG_FILENAME = 100


def tag_filename(tag: str) -> str:
    """
    Return the file name (without .html) for a tag's page.
    """
    name = UNSAFE_CHARACTERS.sub("-", tag)
    # cut by bytes, since that's what filesystems limit, without splitting
    # a character
    name = name.encode()[:MAX_TAG_FILENAME].decode(errors="ignore")
    # Windows drops trailing dots and spaces
    return name.rstrip(". ") or "tag"


def tag_files(tags: Iterable[str]) -> dict[str, str]:
    """
    Map each tag to a file name that no other tag shares, even on a
    case-insensitive filesystem, and that isn't the tags index page.
    """
    taken = {"index"}
    files = {}
    for tag in sorted(set(tags)):
        base = tag_filename(tag)
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{base}-{n}"
        taken.add(name.lower())
        files[tag] = name
    return files


LICENSE_NAMES = {
    "public-domain": "Public domain",
    "open-government": "Open Government Licence",
}

# the statements at https://rightsstatements.org/page/1.0/
RIGHTS_STATEMENTS = {
    "InC": "In Copyright",
    "InC-OW-EU": "In Copyright - EU Orphan Work",
    "InC-EDU": "In Copyright - Educational Use Permitted",
    "InC-NC": "In Copyright - Non-Commercial Use Permitted",
    "InC-RUU": "In Copyright - Rights-holder(s) Unlocatable or Unidentifiable",
    "NoC-CR": "No Copyright - Contractual Restrictions",
    "NoC-NC": "No Copyright - Non-Commercial Use Only",
    "NoC-OKLR": "No Copyright - Other Known Legal Restrictions",
    "NoC-US": "No Copyright - United States",
    "CNE": "Copyright Not Evaluated",
    "UND": "Copyright Undetermined",
    "NKC": "No Known Copyright",
}
RIGHTS_STATEMENT_URL = re.compile(r"rightsstatements\.org/vocab/([A-Za-z-]+)/")

CC_URL = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/([\d.]+)")


def license_label(code: str) -> tuple[str, str | None]:
    """
    Return a readable name for a pin's license, and a link for it if it has
    one, e.g. "CC BY-SA 3.0" for a Creative Commons license URL.
    """
    href = code if code.startswith(("http://", "https://")) else None
    if code in LICENSE_NAMES:
        return LICENSE_NAMES[code], href
    if (match := RIGHTS_STATEMENT_URL.search(code)) and match.group(
        1
    ) in RIGHTS_STATEMENTS:
        return RIGHTS_STATEMENTS[match.group(1)], href
    if match := CC_URL.search(code):
        kind, name, version = match.groups()
        if kind == "publicdomain" and name == "zero":
            return f"CC0 {version}", href
        if kind == "licenses":
            return f"CC {name.upper()} {version}", href
    return code, href


def long_date(timestamp: str) -> str:
    """
    Format an ISO 8601 timestamp as a date like 19 November 2026.
    """
    when = datetime.strptime(timestamp[:10], "%Y-%m-%d")
    return f"{when.day} {when:%B %Y}"


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


env = jinja2.Environment(
    loader=jinja2.PackageLoader("pincushion"), autoescape=jinja2.select_autoescape()
)
env.filters["long_date"] = long_date
env.filters["plural"] = plural
env.filters["host"] = lambda url: urlsplit(url).netloc or url
env.globals["license_label"] = license_label


class ArchiveGenerator:
    def __init__(self, archive_dir: Path, downloader: MediaDownloader | None = None):
        self.archive_dir = archive_dir
        if not self.archive_dir.is_dir():
            raise ValueError(f"No such archive directory: {archive_dir}")

        self.downloader = downloader
        self.data = json.load((self.archive_dir / "data.json").open())
        if "pincushion_version" not in self.data:
            raise ValueError(
                f"{archive_dir} was made by pincushion 0.5 or earlier, which used the old "
                "Historypin API. Fetch it again with pincushion 0.6 or later."
            )

        self.pins: list[dict] = self.data["pins"]
        colls = {coll["id"]: coll for coll in self.data["collections"]}
        self.pins_by_collection: dict[int, list[dict]] = defaultdict(list)
        self.collection_of: dict[int, dict] = {}
        for pin in self.pins:
            for coll_id in pin.get("collection_ids", []):
                if coll_id in colls:
                    self.pins_by_collection[coll_id].append(pin)
                    self.collection_of.setdefault(pin["id"], colls[coll_id])
        self.tag_files = tag_files(
            tag for pin in self.pins for tag in pin.get("tags", [])
        )

    def generate(self) -> None:
        if self.downloader is not None:
            self.download_media()
            self.__dict__.pop("posters", None)
            self.__dict__.pop("downloaded", None)
        media = self.media_types()
        self.write_index(media)
        self.write_collections(media)
        self.write_pins(media)
        self.write_tags(media)
        self.write_map()

    def download_media(self, progress: bool = True) -> None:
        """
        Download any media that isn't in the archive yet.
        """
        downloader = self.downloader
        if downloader is None:
            return
        if self.index_type == "user":
            index_image = self.data["member"].get("image_url")
        else:
            index_image = self.data["collection"].get("banner_image_url")
        downloader.fetch_image(index_image, "index.jpg")

        downloader.pin_images(
            list(tqdm.tqdm(self.pins, desc=f"{'pin images':20}", disable=not progress))
        )
        # before the collection images, which can fall back to a poster
        downloader.all_pin_av(self.pins, progress=progress)
        for coll in self.collections():
            path = f"collections/{coll['id']}/image.jpg"
            if not downloader.fetch_image(coll.get("banner_image_url"), path):
                self.copy_first_pin_image(coll, path)

    def copy_first_pin_image(self, coll: dict, rel_path: str) -> None:
        """
        Use the collection's first pin image, or a video or audio pin's
        poster, when it has no banner.
        """
        dest = self.archive_dir / rel_path
        if dest.is_file():
            return
        for pin in self.pins_by_collection[coll["id"]]:
            pin_dir = self.archive_dir / "pins" / str(pin["id"])
            for image in (pin_dir / "image.jpg", pin_dir / "poster.jpg"):
                if image.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(image, dest)
                    return

    def render(self, template: str, root: str, **context) -> str:
        """
        Render a page whose path back to the archive's top is root, with
        what every page shows: the navigation and where the archive came from.
        """
        return env.get_template(template).render(
            root=root,
            index_title=self.index_title,
            source=urlsplit(self.data.get("site", "")).netloc,
            fetched_at=self.data.get("fetched_at"),
            version=self.data.get("pincushion_version"),
            pin_count=len(self.pins),
            partial=self.data.get("partial"),
            truncated=self.data.get("truncated", []),
            posters=self.posters,
            downloaded=self.downloaded,
            **context,
        )

    @functools.cached_property
    def posters(self) -> set[int]:
        """
        The IDs of video and audio pins with a poster image.
        """
        return {
            pin["id"]
            for pin in self.pins
            if (self.archive_dir / "pins" / str(pin["id"]) / "poster.jpg").is_file()
        }

    @functools.cached_property
    def downloaded(self) -> set[int]:
        """
        The IDs of pins whose image, video or audio is in the archive.
        """
        return {pin["id"] for pin in self.pins if self.has_media(pin)}

    def has_media(self, pin: dict) -> bool:
        """
        Whether the pin's image, video or audio is in the archive.
        """
        pin_dir = self.archive_dir / "pins" / str(pin["id"])
        return any(
            (pin_dir / name).is_file()
            for name in ("image.jpg", "media.mp4", "media.mp3")
        )

    def media_types(self) -> dict[int, str]:
        """
        Map each pin ID to the kind of media in its directory, falling back
        to the pin's type when nothing was downloaded.
        """
        media = {}
        for pin in self.pins:
            pin_dir = self.archive_dir / "pins" / str(pin["id"])
            if (pin_dir / "image.jpg").is_file():
                media[pin["id"]] = "image"
            elif (pin_dir / "media.mp4").is_file():
                media[pin["id"]] = "video"
            elif (pin_dir / "media.mp3").is_file():
                media[pin["id"]] = "audio"
            else:
                media[pin["id"]] = media_type(pin)
        return media

    def write_index(self, media: dict[int, str]) -> None:
        loose = [pin for pin in self.pins if pin["id"] not in self.collection_of]
        html = self.render(
            "index.html",
            "",
            title=self.index_title,
            description=self.index_description,
            has_image=(self.archive_dir / "index.jpg").is_file(),
            collections=list(self.collections()),
            pins=loose,
            media=media,
        )
        self.write(html, "index.html")

    def write_collections(self, media: dict[int, str]) -> None:
        for coll in self.collections():
            html = self.render(
                "collection.html",
                "../../",
                collection=coll,
                pins=self.pins_by_collection[coll["id"]],
                media=media,
                title=coll["title"],
            )
            self.write(html, "collections", str(coll["id"]), "index.html")

    def write_pins(self, media: dict[int, str]) -> None:
        for pin in self.pins:
            html = self.render(
                "pin.html",
                "../../",
                pin=pin,
                media_type=media[pin["id"]],
                media_url=media_url(pin),
                collection=self.collection_of.get(pin["id"]),
                title=pin["title"],
                tag_files=self.tag_files,
            )
            self.write(html, "pins", str(pin["id"]), "index.html")

    def write_tags(self, media: dict[int, str]) -> None:
        tag_index = defaultdict(list)
        for pin in self.pins:
            for tag in pin.get("tags", []):
                tag_index[tag].append(pin)

        by_letter: dict[str, list[str]] = defaultdict(list)
        for tag in sorted(tag_index, key=str.casefold):
            first = tag[:1].upper()
            by_letter[first if first.isalpha() else "#"].append(tag)
        html = self.render(
            "tags.html",
            "../",
            tags_by_letter=sorted(by_letter.items()),
            tag_index=tag_index,
            tag_files=self.tag_files,
            title="Tags",
        )
        self.write(html, "tags", "index.html")

        for tag, pins in tag_index.items():
            html = self.render(
                "tag.html",
                "../",
                tag=tag,
                pins=pins,
                media=media,
                title=f"Tag {tag}",
            )
            self.write(html, "tags", self.tag_files[tag] + ".html")

    def write_map(self) -> None:
        geojson: dict = {"type": "FeatureCollection", "features": []}

        for pin in self.pins:
            if pin.get("latitude") is None or pin.get("longitude") is None:
                continue
            geojson["features"].append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [pin["longitude"], pin["latitude"]],
                    },
                    "properties": {
                        "name": pin["title"],
                        "popupContent": pin["description"],
                        "id": pin["id"],
                    },
                }
            )

        self.write(json.dumps(geojson, indent=2), "map", "pins.geojson")

        html = self.render(
            "map.html",
            "../",
            title="Map",
            # escape < so no title or description can close the script tag
            geojson=json.dumps(geojson, indent=2).replace("<", "\\u003c"),
        )
        self.write(html, "map", "index.html")

        # coastlines and borders, so the map works without tiles
        shutil.copyfile(
            Path(__file__).parent / "templates" / "basemap.js",
            self.archive_dir / "map" / "basemap.js",
        )

        leaflet = Path(__file__).parent / "templates" / "leaflet"
        leaflet_dir = self.archive_dir / "map" / "leaflet"
        if leaflet_dir.is_dir():
            rmtree(leaflet_dir)
        copytree(leaflet, leaflet_dir)

    def write(self, html: str, *path_parts: str) -> None:
        path = self.archive_dir.joinpath(*path_parts)
        path.parent.mkdir(exist_ok=True, parents=True)
        logger.info(f"Writing html to {path}")
        path.write_text(html)

    def collections(self) -> Iterator[dict]:
        """
        The archived collections that have pins in the archive.
        """
        for coll in self.data["collections"]:
            if self.pins_by_collection[coll["id"]]:
                yield coll

    @property
    def index_type(self) -> str:
        return self.data["type"]

    @property
    def index_title(self) -> str:
        if self.index_type == "user":
            member = self.data["member"]
            return member.get("name") or f"Member {member['id']}"
        return self.data["collection"]["title"]

    @property
    def index_description(self) -> str:
        if self.index_type == "user":
            return self.data["member"].get("about") or ""
        coll = self.data["collection"]
        return coll.get("long_description") or coll.get("short_description") or ""
