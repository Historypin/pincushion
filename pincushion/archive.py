from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from shutil import copytree, rmtree

import jinja2
import tqdm

from pincushion.media import MediaDownloader, media_type, media_url

logger = logging.getLogger(__name__)


def tag_filename(tag: str) -> str:
    """
    Return the file name (without .html) for a tag's page.
    """
    # yes, we've noticed some nulls in tags
    return tag.replace("\0", "").replace("/", "-")


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


env = jinja2.Environment(
    loader=jinja2.PackageLoader("pincushion"), autoescape=jinja2.select_autoescape()
)


class ArchiveGenerator:
    def __init__(self, archive_dir: Path, downloader: MediaDownloader | None = None):
        self.archive_dir = archive_dir
        if not self.archive_dir.is_dir():
            raise ValueError(f"No such archive directory: {archive_dir}")

        self.downloader = downloader
        self.data = json.load((self.archive_dir / "data.json").open())
        if "pincushion_version" not in self.data:
            raise ValueError(
                f"{archive_dir} was made by pincushion 0.x, which used the old "
                "Historypin API. Fetch it again with pincushion 1.0."
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
        for coll in self.collections():
            path = f"collections/{coll['id']}/image.jpg"
            if not downloader.fetch_image(coll.get("banner_image_url"), path):
                self.copy_first_pin_image(coll, path)
        downloader.all_pin_av(self.pins, progress=progress)

    def copy_first_pin_image(self, coll: dict, rel_path: str) -> None:
        """
        Use the collection's first pin image when it has no banner.
        """
        dest = self.archive_dir / rel_path
        if dest.is_file():
            return
        for pin in self.pins_by_collection[coll["id"]]:
            image = self.archive_dir / "pins" / str(pin["id"]) / "image.jpg"
            if image.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image, dest)
                return

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
        html = env.get_template("index.html").render(
            title=self.index_title,
            description=self.index_description,
            has_image=(self.archive_dir / "index.jpg").is_file(),
            collections=list(self.collections()),
            pins=loose,
            media=media,
            partial=self.data.get("partial"),
            truncated=self.data.get("truncated", []),
        )
        self.write(html, "index.html")

    def write_collections(self, media: dict[int, str]) -> None:
        tmpl = env.get_template("collection.html")
        for coll in self.collections():
            html = tmpl.render(
                collection=coll,
                pins=self.pins_by_collection[coll["id"]],
                media=media,
                index_title=self.index_title,
                title=coll["title"],
            )
            self.write(html, "collections", str(coll["id"]), "index.html")

    def write_pins(self, media: dict[int, str]) -> None:
        tmpl = env.get_template("pin.html")
        for pin in self.pins:
            html = tmpl.render(
                pin=pin,
                media_type=media[pin["id"]],
                media_url=media_url(pin),
                collection=self.collection_of.get(pin["id"]),
                index_title=self.index_title,
                title=pin["title"],
                tag_files=self.tag_files,
            )
            self.write(html, "pins", str(pin["id"]), "index.html")

    def write_tags(self, media: dict[int, str]) -> None:
        tag_index = defaultdict(list)
        for pin in self.pins:
            for tag in pin.get("tags", []):
                tag_index[tag].append(pin)

        html = env.get_template("tags.html").render(
            tags=sorted(tag_index.keys()),
            tag_index=tag_index,
            tag_files=self.tag_files,
            index_title=self.index_title,
            title="Tags",
        )
        self.write(html, "tags", "index.html")

        tag_tmpl = env.get_template("tag.html")
        for tag, pins in tag_index.items():
            html = tag_tmpl.render(
                tag=tag,
                pins=pins,
                media=media,
                index_title=self.index_title,
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

        html = env.get_template("map.html").render(
            index_title=self.index_title, geojson=json.dumps(geojson, indent=2)
        )
        self.write(html, "map", "index.html")

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
