# pincushion

[![Test](https://github.com/historypin/pincushion/actions/workflows/test.yml/badge.svg)](https://github.com/historypin/pincushion/actions/workflows/test.yml)

*pincushion* is a command line tool for creating archives of resources on [Historypin]. It reads data from the Historypin data export API and writes a static website to a given directory, which you can open in a browser without an Internet connection. The resulting file structure looks something like:

```
archive
├── collections
│   ├── 12
│   │   ├── image.jpg
│   │   └── index.html
│   └── 34
│       ├── image.jpg
│       └── index.html
├── data.json
├── index.html
├── index.jpg
├── map
│   ├── index.html
│   └── pins.geojson
├── pins
│   ├── 123
│   │   ├── image.jpg
│   │   └── index.html
│   ├── 1001
│   │   ├── index.html
│   │   └── media.mp3
│   └── 2112
│       ├── index.html
│       └── media.mp4
└── tags
    ├── index.html
    └── san francisco.html
```

Each pin has its own directory, which contains its uploaded media (image, audio or video). The `data.json` file holds the records fetched from the Historypin API, exactly as the API returned them, and is used to build the static site.

## Install

pincushion needs Python 3.11 or later.

```shell
pip install pincushion
```

pincushion uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download video and audio. Sites like YouTube change often, and an old yt-dlp soon stops working with them, so upgrade it before making an archive:

```shell
pip install --upgrade yt-dlp
```

Some media can't be downloaded at all, for example SoundCloud tracks the rights holder protects with DRM. The pin's page then says so and links to the original.

## Get an API key

pincushion needs a Historypin API key:

1. Log in to Historypin and go to <https://historypin.org/api-keys/>.
2. Click "Generate New Key" and copy the key. It's only shown once.

API access is a feature that a Historypin admin turns on for your account. Without it, the API refuses your key and pincushion tells you so.

pincushion reads the key from the `HISTORYPIN_KEY` environment variable, or from the `--api-key` option. If you keep it in a `.env` file, load it into your shell first:

```shell
set -a; . .env; set +a
```

By default pincushion talks to <https://historypin.org>. To use another Historypin site, such as a local copy, set `HISTORYPIN_SITE` or pass `--site`:

```shell
pincushion user --site http://localhost:8000
```

## Use

Create an archive of your own pins and collections:

```shell
pincushion user --archive-path my-archive
```

Open `my-archive/index.html` in your browser.

The whole archive works offline, including the map, which draws coastlines and country borders from [Natural Earth](https://www.naturalearthdata.com/) data stored in the archive. When you serve the archive from a web server and you're online, the map also shows OpenStreetMap's street-level detail. OpenStreetMap's servers don't answer pages opened straight from a file. To serve the archive with Python:

```shell
cd my-archive
python3 -m http.server
```

Then open <http://localhost:8000/>.

Create an archive of a collection and all its sub-collections, using the collection's ID (the number in its Historypin URL, as in `/collections/1234/`):

```shell
pincushion collection --collection-id 1234 --archive-path my-collection
```

### What your key can archive

- **A regular key** archives your own pins, and the collections you own or manage. A collection archive made with a regular key includes only your own pins in that collection. pincushion warns you, and says on the archive's front page, when it leaves out pins or collections the key can't see.
- **A staff key** can archive any member (`pincushion user --user-id N`) or a whole collection, including everyone's pins. An archive of another member includes the collections they own, but not ones they only manage.

`--user-id` is the member's user ID, which is **not** the number in their profile's URL (`/members/<n>/`). You can find it in the Historypin admin, or as the `creator_id` of one of their pins in the API. pincushion prints the name of the member it's archiving when it starts, so you can stop it if it's the wrong person.

The archive doesn't include comments or tours, because the export API doesn't provide them yet. The API returns at most 10,000 items in a list, and pincushion warns you if a list reaches that limit.

Requests are rate limited (100 an hour for a regular key, 1000 for a staff key). When pincushion hits the limit, it waits until it can continue, which can take up to an hour.

### Other commands

- `pincushion regenerate --archive-path DIR` rebuilds the website from an existing archive's `data.json`, without fetching the data again.
- `pincushion media --archive-path DIR` downloads any media that's missing from an archive.
- `pincushion user-data --output FILE` and `pincushion collection-data --collection-id N --output FILE` save the JSON without building a website.

The commands that download video and audio accept `--cookie-file`, which is passed to [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

## Upgrading from 0.5 or earlier

pincushion 0.6 works with the new Historypin site's API. Archives made with 0.5 or earlier used the old Historypin API, and `regenerate` can't read them, so fetch them again with 0.6.

[Historypin]: https://historypin.org
