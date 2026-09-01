#!/usr/bin/env python3
"""Digital picture frame web application."""

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import yaml
from flask import (
    Flask, abort, jsonify, render_template, request, send_from_directory,
)
from PIL import Image, ImageOps

from db import get_db, init_db
from playlist import get_playlist_photos, load_playlists, validate_playlists

app = Flask(__name__)


@contextmanager
def _safe_write(action):
    """Run a DB write, swallowing lock/FK errors so a mid-index delete or lock
    timeout never crashes the slideshow."""
    try:
        with get_db() as conn:
            yield conn
    except sqlite3.Error as e:
        app.logger.warning("skipped %s: %s", action, e)


def _photo_exists(photo):
    return photo is not None and os.path.exists(photo["path"])


# ---------------------------------------------------------------------------
# Slideshow state
# ---------------------------------------------------------------------------

class FrameState:
    """Mutable slideshow state shared across requests."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.photos = []
        self.index = -1
        self.paused = False
        self.current_photo = None
        self.last_change = time.time()

        playlists = load_playlists(config)
        default = config.get("default_playlist")
        if default and default in playlists:
            self.active_playlist_id = default
        else:
            self.active_playlist_id = next(iter(playlists), None)
        if self.active_playlist_id:
            self.load_playlist(self.active_playlist_id)

    def load_playlist(self, playlist_id):
        playlists = load_playlists(self.config)
        if playlist_id not in playlists:
            return False
        playlist_config = playlists[playlist_id]
        shuffle = playlist_config.get("shuffle",
                                      self.config.get("display", {}).get("shuffle", True))
        global_exclude = self.config.get("exclude")
        self.active_playlist_id = playlist_id
        self.photos = get_playlist_photos(
            playlist_config, shuffle=shuffle, global_exclude=global_exclude,
            mount_point=self.config["nas"]["mount_point"])
        self.index = -1
        self.advance()
        return True

    def advance(self):
        return self._step(1)

    def go_prev(self):
        return self._step(-1)

    def _step(self, delta):
        """Move by delta, skipping photos whose file is gone from disk."""
        if not self.photos:
            self.current_photo = None
            return None
        for _ in range(len(self.photos)):          # bounded: at most one full pass
            self.index = (self.index + delta) % len(self.photos)
            candidate = self.photos[self.index]
            if _photo_exists(candidate):
                self.current_photo = candidate
                self.last_change = time.time()
                self._record_history()
                return candidate
        self.current_photo = None                  # every file is gone → show nothing
        return None

    def _record_history(self):
        if not self.current_photo:
            return
        hist = self.config.get("history", {})
        if not hist.get("enabled", True):
            return

        now = datetime.now(timezone.utc).isoformat()
        with _safe_write("history record") as conn:
            conn.execute(
                "INSERT INTO history (photo_id, playlist, played_at) VALUES (?, ?, ?)",
                (self.current_photo["id"], self.active_playlist_id, now),
            )
            max_entries = hist.get("max_entries", 500)
            conn.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY played_at DESC LIMIT ?)",
                (max_entries,),
            )


def _load_config(path="config.yaml"):
    with open(path) as f:
        config = yaml.safe_load(f)
    validate_playlists(load_playlists(config))
    return config


config = _load_config()

# Downscaled-image cache. Full-resolution originals are expensive for the Pi's
# GPU to decode and composite at 4K, so the frame is served local, downscaled
# derivatives instead. The cache lives on local disk and is bounded by max_bytes.
_cache_cfg = config.get("cache") or {}
CACHE_DIR = os.path.abspath(_cache_cfg.get("dir", "cache"))
DISPLAY_CACHE_DIR = os.path.join(CACHE_DIR, "display")
CACHE_MAX_EDGE = int(_cache_cfg.get("max_edge", 3840))
CACHE_BG_EDGE = int(_cache_cfg.get("bg_edge", 320))
CACHE_QUALITY = int(_cache_cfg.get("jpeg_quality", 85))
CACHE_MAX_BYTES = int(_cache_cfg.get("max_bytes", 16_000_000_000))
os.makedirs(DISPLAY_CACHE_DIR, exist_ok=True)

init_db()
state = FrameState(config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _photo_response(photo):
    if photo is None:
        return None
    p = dict(photo)
    mount = os.path.abspath(config["nas"]["mount_point"])
    rel = os.path.relpath(photo["path"], mount)
    p["relative_path"] = rel
    # The frame loads the downscaled derivative; the blurred background reuses
    # this URL with ?bg=1 (a much smaller thumbnail). See serve_display.
    p["url"] = f"/display/{rel}"
    if p.get("date_taken"):
        p["date_taken"] = p["date_taken"][:10]  # YYYY-MM-DD only
    return p


def _position_fields():
    """Current 1-based position and total count of the active playlist.

    Caller must hold state.lock. total is 0 for an empty playlist; the client
    hides the indicator when total is 0 or there is no current photo.
    """
    return {"position": state.index + 1, "total": len(state.photos)}


def _effective_display(key, default):
    """Display setting with per-playlist override."""
    playlists = load_playlists(config)
    pl = playlists.get(state.active_playlist_id, {})
    if key in pl:
        return pl[key]
    return config.get("display", {}).get(key, default)


def _in_time_window(current_time, window):
    start = window.get("start", "00:00")
    end = window.get("end", "00:00")
    if start <= end:
        return start <= current_time < end
    return current_time >= start or current_time < end


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def slideshow():
    return render_template("slideshow.html")


@app.route("/remote")
def remote():
    return render_template("remote.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/playlists")
def api_playlists():
    playlists = load_playlists(config)
    return jsonify([
        {"id": pid, "name": pc.get("name", pid), "active": pid == state.active_playlist_id}
        for pid, pc in playlists.items()
    ])


@app.route("/api/now-playing")
def api_now_playing():
    with state.lock:
        return jsonify({
            "photo": _photo_response(state.current_photo),
            "playlist": state.active_playlist_id,
            "paused": state.paused,
            **_position_fields(),
            "interval": _effective_display("interval_secs", 30),
            "display": {
                "fit_mode": _effective_display("fit_mode", "fit"),
                "background": _effective_display("background", "black"),
                "transition": _effective_display("transition", "fade"),
                "transition_duration": _effective_display("transition_duration_secs", 1.5),
                "show_info_overlay": _effective_display("show_info_overlay", False),
            },
        })


@app.route("/api/control/next", methods=["POST"])
def api_next():
    with state.lock:
        photo = state.advance()
        position = _position_fields()
    return jsonify({"photo": _photo_response(photo), **position})


@app.route("/api/control/prev", methods=["POST"])
def api_prev():
    with state.lock:
        photo = state.go_prev()
        position = _position_fields()
    return jsonify({"photo": _photo_response(photo), **position})


@app.route("/api/control/pause", methods=["POST"])
def api_pause():
    with state.lock:
        state.paused = not state.paused
    return jsonify({"paused": state.paused})


@app.route("/api/control/playlist/<playlist_id>", methods=["POST"])
def api_switch_playlist(playlist_id):
    with state.lock:
        if not state.load_playlist(playlist_id):
            abort(404, description="Playlist not found")
        return jsonify({
            "playlist": playlist_id,
            "photo": _photo_response(state.current_photo),
            **_position_fields(),
        })


@app.route("/api/control/reload", methods=["POST"])
def api_reload():
    """Rebuild the active playlist from the DB — call after a reindex."""
    with state.lock:
        if not state.active_playlist_id:
            return jsonify({"reloaded": False})
        state.load_playlist(state.active_playlist_id)
        return jsonify({
            "reloaded": True,
            "count": len(state.photos),
            "photo": _photo_response(state.current_photo),
            **_position_fields(),
        })


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", 50, type=int)
    mount = os.path.abspath(config["nas"]["mount_point"])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT h.id, h.playlist, h.played_at, h.duration_shown,
                   p.id as photo_id, p.path, p.rating, p.keywords, p.people,
                   p.orientation
            FROM history h
            JOIN photos p ON h.photo_id = p.id
            ORDER BY h.played_at DESC LIMIT ?
        """, (limit,)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["url"] = f"/photos/{os.path.relpath(d['path'], mount)}"
        for key in ("keywords", "people"):
            d[key] = json.loads(d[key]) if d[key] else []
        result.append(d)
    return jsonify(result)


@app.route("/api/photos/<int:photo_id>/rating", methods=["PUT"])
def api_update_rating(photo_id):
    data = request.get_json()
    rating = data.get("rating")
    if rating is not None and (not isinstance(rating, int) or rating < 0 or rating > 5):
        abort(400, description="Rating must be an integer 0-5")

    try:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if not row:
                abort(404, description="Photo not found")
            conn.execute("UPDATE photos SET rating = ? WHERE id = ?", (rating, photo_id))
            path = row["path"]
    except sqlite3.Error as e:
        app.logger.warning("rating update failed for photo %s: %s", photo_id, e)
        abort(503, description="Database busy, try again")

    # Write rating back to JPEG via exiftool
    try:
        subprocess.run(
            ["exiftool", "-overwrite_original", f"-Rating={rating}", path],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        app.logger.warning("exiftool writeback failed for %s: %s", path, e.stderr)

    with state.lock:
        if state.current_photo and state.current_photo["id"] == photo_id:
            state.current_photo["rating"] = rating

    return jsonify({"id": photo_id, "rating": rating})


def _safe_photo_path(filepath):
    """Resolve a request path to an absolute file inside the NAS mount, or abort."""
    mount = os.path.abspath(config["nas"]["mount_point"])
    full = os.path.realpath(os.path.join(mount, filepath))
    if full != mount and not full.startswith(mount + os.sep):
        abort(403)
    return full


def _cache_path(src, edge):
    """Local cache filename for a derivative, invalidated by the source mtime."""
    key = f"{src}:{os.path.getmtime(src)}:{edge}:{CACHE_QUALITY}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(DISPLAY_CACHE_DIR, f"{digest}.jpg")


def _render_scaled(src, dst, edge):
    """Decode src, downscale to fit edge x edge, and write a JPEG to dst."""
    with Image.open(src) as im:
        im.draft("RGB", (edge, edge))          # DCT-scaled decode = fast
        im = ImageOps.exif_transpose(im)       # bake in EXIF orientation
        im.thumbnail((edge, edge), Image.Resampling.LANCZOS)  # downscale-only; never enlarges
        im.convert("RGB").save(
            dst, "JPEG", quality=CACHE_QUALITY, optimize=True, progressive=True)


def _enforce_cache_cap():
    """Evict least-recently-used cache files until under 90% of the size cap."""
    entries = []
    total = 0
    for name in os.listdir(DISPLAY_CACHE_DIR):
        path = os.path.join(DISPLAY_CACHE_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        entries.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    if total <= CACHE_MAX_BYTES:
        return
    target = int(CACHE_MAX_BYTES * 0.9)
    entries.sort()  # oldest mtime first
    for _, size, path in entries:
        if total <= target:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


def _serve_scaled(filepath, edge):
    src = _safe_photo_path(filepath)
    if not os.path.isfile(src):
        abort(404)
    # Downscale-only: images already within the target are streamed as-is, so the
    # cache only ever holds the large-image tail. (Chromium honors EXIF orientation
    # for <img>.) Background thumbnails (small edge) are always generated.
    if edge >= CACHE_MAX_EDGE:
        try:
            with Image.open(src) as im:
                already_small = max(im.size) <= edge
        except OSError:
            abort(404)
        if already_small:
            return send_from_directory(os.path.dirname(src), os.path.basename(src))
    dst = _cache_path(src, edge)
    if os.path.exists(dst):
        os.utime(dst, None)  # touch for LRU: "recently shown" survives eviction
    else:
        _render_scaled(src, dst, edge)
        _enforce_cache_cap()
    return send_from_directory(DISPLAY_CACHE_DIR, os.path.basename(dst))


@app.route("/photos/<path:filepath>")
def serve_photo(filepath):
    full = _safe_photo_path(filepath)
    return send_from_directory(os.path.dirname(full), os.path.basename(full))


@app.route("/display/<path:filepath>")
def serve_display(filepath):
    """Downscaled derivative for the frame; ?bg=1 returns a tiny blur thumbnail."""
    edge = CACHE_BG_EDGE if request.args.get("bg") else CACHE_MAX_EDGE
    return _serve_scaled(filepath, edge)


@app.route("/api/schedule")
def api_schedule():
    now = datetime.now().strftime("%H:%M")
    sched = config.get("schedule", {})
    night = sched.get("night_mode", {})
    power = sched.get("power_save", {})
    return jsonify({
        "night_mode": _in_time_window(now, night) if night.get("enabled") else False,
        "night_brightness": night.get("brightness", 0.3),
        "power_save": _in_time_window(now, power) if power.get("enabled") else False,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
