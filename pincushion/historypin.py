import logging
import time
from functools import cache
from typing import Optional, Generator, Dict, Any, Union

import requests
import tqdm

logger = logging.getLogger(__name__)


@cache
def get_user(user_id: int, progress: bool = True) -> dict:
    data: Dict[str, Union[dict, list]] = {}
    data["user"] = get_json("user/get", {"id": user_id})

    # add this user's collections, tours and pins
    data["collections"] = list(
        get_listing(user_id, "projects", "collection", progress=progress)
    )
    data["tours"] = list(get_listing(user_id, "projects", "tour", progress=progress))
    data["pins"] = list(get_listing(user_id, "pin", progress=progress))

    # get the comments on each of their pins
    if progress:
        desc = "comments"
        bar = tqdm.tqdm(desc=f"{desc:20}", total=len(data["pins"]), leave=True)
    else:
        bar = None

    for pin in data["pins"]:
        pin["comments"] = get_comments(pin["id"])
        if bar is not None:
            bar.update(1)

    return data


def get_listing(
    user_id: int,
    listing_type: str,
    item_type: Optional[str] = None,
    progress: bool = True,
) -> Generator[dict, None, None]:
    """
    Iterate through a Historypin listing for a user by type (projects or pin). For projects
    you need to further specify whether you want collections or tours.
    """
    params: Dict[str, Any] = {"user": user_id, "page": 0, "limit": 100}

    if item_type is not None:
        params["type"] = item_type

    if progress:
        count = get_json(f"{listing_type}/listing", params)["count"]
        desc = (item_type or listing_type) + "s"
        bar = tqdm.tqdm(desc=f"{desc:20}", total=count, leave=True)
    else:
        bar = None

    while True:
        params["page"] += 1
        page = get_json(f"{listing_type}/listing", params)
        if len(page["results"]) == 0:
            break

        for result in page["results"]:
            if bar is not None:
                bar.update(1)

            # get and return the full item metadata for the resource
            if listing_type == "projects":
                yield get_json(f"{result['slug']}/projects/get", {})
            elif listing_type == "pin":
                yield get_pin(result["id"])


@cache
def get_pin(pin_id) -> dict:
    return get_json("pin/get", {"id": pin_id})


@cache
def get_collection(slug: str, progress: bool = True) -> dict:
    """
    Get a collection and all its pins and subcollections.
    """

    coll = get_json(f"{slug}/projects/get", params={})

    collections = []
    pins = []

    if progress:
        count = coll["counts"]["collections"] + coll["counts"]["pins"]
        desc = coll["slug"]
        bar = tqdm.tqdm(desc=f"{desc:20}", total=count, leave=True)
    else:
        bar = None

    # use the gallery to see what collections and/or pins are in a collection
    for result in get_gallery(slug):
        if result["node_type"] == "pin":
            pin = get_pin(result["id"])
            pin["comments"] = get_comments(result["id"])
            pins.append(pin)
            if bar is not None:
                bar.update(1)

        elif result["node_type"] == "project":
            # this is recursive since a collection can contain collections!
            collections.append(get_collection(f"{slug}/{result['slug']}"))
            if bar is not None:
                bar.update(1)

    # flatten hierarchical collections into a single deduplicated list
    coll["collections"] = list(_unique_collections(collections))
    coll["pins"] = pins

    return coll


def get_gallery(slug: str) -> Generator[dict, None, None]:
    page = 1
    while True:
        data = get_json(f"{slug}/pin/get_gallery", params={"paging": page})
        if len(data["results"]) == 0:
            break

        for result in data["results"]:
            if result["node_type"] in ["project", "pin"]:
                yield result

        page += 1


@cache
def get_comments(pin_id) -> dict:
    return get_json("comments/get", {"item_id": pin_id})["comments"]


def get_json(api_path: str, params: dict, sleep=0.25) -> dict:
    time.sleep(sleep)
    url = f"https://www.historypin.org/en/api/{api_path}.json"
    logger.info(f"fetching {url} {params}")
    return requests.get(url, params=params).json()


def _flatten_collections(collections) -> Generator[dict, None, None]:
    """
    A generator that iterates through each collection and subcollection in a
    list of collections.
    """
    for coll in collections:
        yield from _flatten_collections(coll["collections"])
        yield coll


def _unique_collections(collections) -> Generator[dict, None, None]:
    """
    Generate a list of unique collections in a list of collections.
    """
    seen = set()
    for coll in _flatten_collections(collections):
        if coll["id"] not in seen:
            yield coll
            seen.add(coll["id"])
