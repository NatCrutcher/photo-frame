#!/usr/bin/env python3
"""Digital picture frame web application."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

import yaml
from flask import (
    Flask, abort, jsonify, render_template, request, send_from_directory,
)

from db import get_db, init_db, row_to_dict
from playlist import get_playlist_photos, load_playlists

app = Flask(__name__)


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
            playlist_config, shuffle=shuffle, global_exclude=global_exclude)
        self.index = -1
        self.advance()
        return True

    def advance(self):
        if not self.photos:
            self.current_photo = None
            return None
        self.index = (self.index + 1) % len(self.photos)
        self.current_photo = self.photos[self.index]
        self.last_change = time.time()
        self._record_history()
        return self.current_photo

    def go_prev(self):
        if not self.photos:
            return None
        self.index = (self.index - 1) % len(self.photos)
        self.current_photo = self.photos[self.index]
        self.last_change = time.time()
        self._record_history()
        return self.current_photo

    def _record_history(self):
        if not self.current_photo:
            return
        hist = self.config.get("history", {})
        if not hist.get("enabled", True):
            return

        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
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
        return yaml.safe_load(f)


config = _load_config()
init_db()
state = FrameState(config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _photo_url(photo):
    mount = config["nas"]["mount_point"]
    rel = os.path.relpath(photo["path"], os.path.abspath(mount))
    return f"/photos/{rel}"


def _photo_response(photo):
    if photo is None:
        return None
    p = dict(photo)
    p["url"] = _photo_url(photo)
    mount = os.path.abspath(config["nas"]["mount_point"])
    p["relative_path"] = os.path.relpath(photo["path"], mount)
    return p


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
    return jsonify({"photo": _photo_response(photo)})


@app.route("/api/control/prev", methods=["POST"])
def api_prev():
    with state.lock:
        photo = state.go_prev()
    return jsonify({"photo": _photo_response(photo)})


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

    with get_db() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            abort(404, description="Photo not found")
        conn.execute("UPDATE photos SET rating = ? WHERE id = ?", (rating, photo_id))
        path = row["path"]

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


@app.route("/photos/<path:filepath>")
def serve_photo(filepath):
    mount = os.path.abspath(config["nas"]["mount_point"])
    full = os.path.realpath(os.path.join(mount, filepath))
    if not full.startswith(mount):
        abort(403)
    return send_from_directory(os.path.dirname(full), os.path.basename(full))


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
