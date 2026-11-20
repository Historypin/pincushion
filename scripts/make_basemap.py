"""
Trim Natural Earth GeoJSON into pincushion's map basemap.

Download ne_110m_land, ne_110m_lakes and ne_110m_admin_0_boundary_lines_land
from https://github.com/nvkelso/natural-earth-vector/tree/master/geojson into
a directory, then run:

    python scripts/make_basemap.py DIR pincushion/templates/basemap.js
"""

import json
import sys

src, out = sys.argv[1], sys.argv[2]


def trim(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 2), round(coords[1], 2)]
    return [trim(c) for c in coords]


def load(name):
    with open(f"{src}/{name}.geojson") as fh:
        data = json.load(fh)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": f["geometry"]["type"],
                    "coordinates": trim(f["geometry"]["coordinates"]),
                },
            }
            for f in data["features"]
        ],
    }


layers = {
    "land": load("ne_110m_land"),
    "lakes": load("ne_110m_lakes"),
    "borders": load("ne_110m_admin_0_boundary_lines_land"),
}
with open(out, "w") as fh:
    fh.write(
        "// Coastlines, lakes and country borders from Natural Earth (1:110m),\n"
        "// which is in the public domain: https://www.naturalearthdata.com/\n"
        "// pincushion draws them so the map works offline.\n"
    )
    fh.write("window.PINCUSHION_BASEMAP = ")
    json.dump(layers, fh, separators=(",", ":"))
    fh.write(";\n")
