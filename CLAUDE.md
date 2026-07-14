# CLAUDE.md

Guidance for working in this repo. See `README.md` for the user-facing overview
and `USAGE.md` for deployment/operations detail; this file covers what's useful
when *changing* the code.

## What this is

A DIY digital picture frame. Photos live on a Synology NAS (exported from
Adobe Bridge with embedded XMP metadata) and are displayed full-screen on a
Raspberry Pi 5 running Chromium in kiosk mode against a local Flask app.

Data flow: **Adobe → JPEG+XMP on NAS → `indexer.py` → SQLite (`frame.db`) →
`app.py` (Flask) → browser slideshow / phone remote**.

## Components

- **`indexer.py`** — standalone script. Walks the NAS mount, batch-reads metadata
  via `exiftool -json`, upserts rows into the `photos` table (ON CONFLICT DO
  UPDATE, preserving `id`), and deletes rows for files no longer on disk. Run
  manually or via the nightly systemd timer. Takes ~5 min on a desktop, ~15 min
  on the Pi (~33k photos). On completion it best-effort POSTs
  `/api/control/reload` to a running app (`notify_app_reload`).
- **`app.py`** — the Flask app. Holds all slideshow state in a single in-process
  `FrameState` (guarded by `state.lock`). Serves the slideshow page, the remote,
  the JSON control API, and downscaled image derivatives.
- **`db.py`** — SQLite connection helpers. `get_connection()` sets the pragmas
  (WAL, `busy_timeout=5000`, `foreign_keys=ON`); `get_db()` is a commit-on-exit
  context manager; `row_to_dict()` JSON-decodes `keywords`/`people`.
- **`playlist.py`** — pure DB-query engine. Translates a playlist's `filter`
  config into a SQL `WHERE` and returns matching photo dicts. No filesystem access.
- **`static/slideshow.js`** — the frame UI. Two client-side loops: an advance
  timer (`POST /api/control/next` every `interval` s) and a 3 s `/api/now-playing`
  poll that keeps the frame in sync with remote-driven changes.
- **`static/remote.js`** — the phone remote (a control surface; it never advances
  on its own, it just POSTs control endpoints and polls now-playing).
- **`deploy/`** — systemd units: `photo-frame.service` (the app, `Restart=always`),
  `photo-frame-kiosk.service` (Chromium), `photo-index.{service,timer}` (nightly
  3 a.m. reindex).

## Data model (`db.py:init_db`)

- `photos(id, path UNIQUE, rating, keywords, people, width, height, orientation,
  file_modified_at, date_taken, indexed_at)`. `keywords`/`people` are JSON arrays
  stored as TEXT; playlist filters match them with `LIKE '%"value"%'`. `path` is
  always absolute.
- `history(id, photo_id → photos.id, playlist, played_at, duration_shown)`.
  Written on every advance; capped to `history.max_entries`.

## Key architectural facts (read before changing playback/indexing)

- **The active playlist is fully materialized in memory.** `FrameState.photos` is
  the entire result of `get_playlist_photos()` (e.g. ~33k dicts / ~36 MB for the
  `all` playlist), loaded once at startup and rebuilt on playlist switch or
  `/api/control/reload`. Advancing is O(1) index math; it does **not** re-query.
  The list does not reflect DB changes until a reload/switch/restart.
- **The app and indexer write the same SQLite DB concurrently and this is
  intended to be safe.** Keep it that way when touching write paths:
  - All connections go through `get_connection()` (WAL + `busy_timeout`). Do not
    open raw `sqlite3.connect` elsewhere.
  - App-side DB writes that can race the indexer must not crash the request.
    History writes go through `_safe_write()` (swallows `sqlite3.Error` — lock
    timeout or FK violation from a mid-index delete). The rating endpoint returns
    503 on a lock error. Follow this pattern for any new write path.
  - `foreign_keys=ON`, so a `history` insert for a photo the indexer just deleted
    raises `IntegrityError` — that's why the wrapper exists.
- **Deleted-photo resilience.** The playlist can contain photos whose files were
  removed off the NAS since it was built. `FrameState._step()` skips any photo
  failing `_photo_exists()` (bounded to one pass; returns `None` if all are gone).
  The frontend is a second layer: `slideshow.js` auto-advances on image
  load/decode failure (capped at 5 consecutive), and `remote.js` hides broken
  images. `/display/*` returns 404 for missing files (`_serve_scaled`).
- **The app never auto-advances server-side.** The pointer only moves when a
  client POSTs `/api/control/*`. Both frame and remote stay in sync via the 3 s
  now-playing poll.

## Image serving

`/display/<path>` serves a cached, downscaled JPEG derivative (4K long-edge for the
frame, 320 px for the blurred background via `?bg=1`). Cache lives in `cache/`,
keyed by source path + mtime, LRU-evicted under `cache.max_bytes`. Images already
within the target edge are streamed as-is (`_serve_scaled`). This exists because
decoding/compositing full-res originals at 4K stutters on the Pi GPU.

## Config

`config.yaml` (gitignored; template is `config_sample.yaml`). Loaded once at
startup — **restart the app to pick up config changes.** Notable keys:
`nas.mount_point`, `playlists` (each with a `filter`), global/per-playlist
`exclude`, `display` (interval, fit, transition, background), `schedule`
(night_mode/power_save windows), `cache`, `history`, and optional
`app.reload_url` (indexer's reload target; defaults to `http://localhost:5000`).

## Dev workflow

```sh
pip install -r requirements.txt      # flask, pyyaml, Pillow (exiftool is a system dep)
python3 app.py                       # dev server on :5000, debug=True
python3 indexer.py config.yaml       # (re)build frame.db from the NAS mount
```

There is no test suite yet. To sanity-check changes, run the app and exercise the
JSON API with `curl` (e.g. `POST /api/control/reload`, `/api/now-playing`); the
playlist/skip logic can be driven directly by importing `app`/`playlist` and
manipulating `state.photos` (importing `app.py` does not start the server — that's
under `__main__`).

## Conventions

- `frame.db`, `cache/`, `config.yaml`, and `plans/` are gitignored. Don't commit
  a real `config.yaml`; update `config_sample.yaml` when adding config keys.
- Keep functions short and single-purpose (see the existing helpers). New DB
  writes reuse `get_db()`/`_safe_write()` rather than opening connections directly.
- Use `git rm` when deleting tracked files.
