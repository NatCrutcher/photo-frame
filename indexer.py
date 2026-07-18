#!/usr/bin/env python3
"""Scan a photo directory, extract metadata with exiftool, and populate SQLite."""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

import yaml

from db import get_db, init_db

BATCH_SIZE = 100
DEFAULT_RELOAD_URL = "http://localhost:5000/api/control/reload"


def notify_app_reload(config):
    """Best-effort: tell a running frame app to reload the active playlist.

    Safe to call whether or not the app is up — a down app is not an error, so
    manual daytime re-indexes refresh the frame within a few seconds just like
    the nightly timer does.
    """
    url = config.get("app", {}).get("reload_url", DEFAULT_RELOAD_URL)
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5)
        print("Signaled app to reload")
    except OSError:
        pass  # app not running or unreachable — nothing to refresh


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


# Directory names to skip entirely (Synology recycle bin).
EXCLUDED_DIRS = {"#recycle"}


def scan_photos(directory):
    """Return all JPEG file paths under directory, skipping EXCLUDED_DIRS."""
    paths = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in sorted(files):
            if name.lower().endswith((".jpg", ".jpeg")):
                paths.append(os.path.join(root, name))
    return paths


def read_metadata_batch(paths):
    """Use exiftool in batch mode to read metadata for all paths."""
    if not paths:
        return []

    result = subprocess.run(
        [
            "exiftool", "-json",
            "-Rating", "-Subject", "-RegionName",
            "-ImageWidth", "-ImageHeight", "-FileModifyDate",
            "-DateTimeOriginal", "-CreateDate", "-DateCreated",
        ] + paths,
        capture_output=True, text=True,
    )

    if result.returncode not in (0, 1):  # 1 = minor warnings
        print(f"exiftool error: {result.stderr}", file=sys.stderr)
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Failed to parse exiftool output", file=sys.stderr)
        return []


def parse_list_field(value):
    """Normalize a field that may be a string or list into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_exif_datetime(raw):
    """Normalize an exiftool datetime ("YYYY:MM:DD HH:MM:SS" +opt tz) to ISO."""
    date_part = raw[:10].replace(":", "-")
    time_part = raw[11:19] if len(raw) >= 19 else "00:00:00"
    return f"{date_part} {time_part}"


def _is_plausible_date(iso):
    """True if an ISO "YYYY-MM-DD ..." string is a real calendar date >= 1900.

    Guards against degenerate EXIF values like "0000:00:00 00:00:00", which some
    cameras/edits write; those should fall through to path inference.
    """
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").year >= 1900
    except ValueError:
        return False


# Leading-date patterns for folder names, most specific first. Anchored at the
# start so only a leading date counts (a "2019" buried in a label is ignored).
_FULL_DATE_RE = re.compile(r"^(\d{4})[_-](\d{2})[_-](\d{2})")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})[_-](\d{2})(?:\D|$)")
_YEAR_RE = re.compile(r"^(\d{4})(?:\D|$)")


def _date_from_component(name):
    """Infer an ISO date from a single path component, or None.

    Tiered by precision: full YYYY[_-]MM[_-]DD, else YYYY[_-]MM (month, day->01),
    else a bare leading year (month/day->01). Out-of-range values are rejected.
    """
    m = _FULL_DATE_RE.match(name)
    if m:
        year, month, day = (int(g) for g in m.groups())
        if 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d} 00:00:00"
    m = _YEAR_MONTH_RE.match(name)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1900 <= year <= 2099 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-01 00:00:00"
    m = _YEAR_RE.match(name)
    if m:
        year = int(m.group(1))
        if 1900 <= year <= 2099:
            return f"{year:04d}-01-01 00:00:00"
    return None


def infer_date_from_path(path):
    """Infer a capture date from the directory names in a photo's path.

    Most photos are filed under `/<year>/<YYYY_MM_DD label>/...`. Walk the
    directories from deepest to shallowest (inner subfolders like `DNG` or
    `Original JPEG` carry no date; the top-level year folder is the last resort)
    and return the first component that starts with a date. None if none match.
    """
    dirs = os.path.dirname(os.path.abspath(path)).split(os.sep)
    for name in reversed(dirs):
        iso = _date_from_component(name)
        if iso:
            return iso
    return None


def resolve_date_taken(meta, path):
    """Best available capture date (ISO), from metadata then the file path.

    Metadata capture date wins; when absent, infer from the directory path.
    Returns None when no date can be determined, so date filters exclude the
    photo. FileModifyDate is deliberately not used — bulk re-exports make it the
    edit/copy time, not the capture time.
    """
    raw = (
        meta.get("DateTimeOriginal")
        or meta.get("CreateDate")
        or meta.get("DateCreated")
    )
    if raw:
        iso = _normalize_exif_datetime(raw)
        if _is_plausible_date(iso):
            return iso
    return infer_date_from_path(path)


def compute_orientation(width, height):
    if width is None or height is None:
        return None
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def index_photos(config):
    mount_point = config["nas"]["mount_point"]

    if not os.path.isdir(mount_point):
        print(f"Photo directory not found: {mount_point}", file=sys.stderr)
        sys.exit(1)

    init_db()

    print(f"Scanning {mount_point}...")
    paths = scan_photos(mount_point)
    print(f"Found {len(paths)} JPEG files")

    if not paths:
        return

    all_metadata = []
    for i in range(0, len(paths), BATCH_SIZE):
        batch = paths[i:i + BATCH_SIZE]
        all_metadata.extend(read_metadata_batch(batch))
        done = min(i + BATCH_SIZE, len(paths))
        print(f"  Read metadata: {done}/{len(paths)}")

    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        indexed_paths = set()

        for meta in all_metadata:
            path = meta.get("SourceFile")
            if not path:
                continue

            indexed_paths.add(os.path.abspath(path))

            rating = meta.get("Rating")
            keywords = parse_list_field(meta.get("Subject"))
            people = parse_list_field(meta.get("RegionName"))
            width = meta.get("ImageWidth")
            height = meta.get("ImageHeight")
            orientation = compute_orientation(width, height)
            file_modified = meta.get("FileModifyDate")
            date_taken = resolve_date_taken(meta, path)

            conn.execute("""
                INSERT INTO photos (path, rating, keywords, people, width, height,
                                    orientation, file_modified_at, date_taken, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    rating = excluded.rating,
                    keywords = excluded.keywords,
                    people = excluded.people,
                    width = excluded.width,
                    height = excluded.height,
                    orientation = excluded.orientation,
                    file_modified_at = excluded.file_modified_at,
                    date_taken = excluded.date_taken,
                    indexed_at = excluded.indexed_at
            """, (
                os.path.abspath(path), rating,
                json.dumps(keywords), json.dumps(people),
                width, height, orientation, file_modified, date_taken, now,
            ))

        # Remove photos that no longer exist on disk
        existing = {row[0] for row in conn.execute("SELECT path FROM photos").fetchall()}
        deleted = existing - indexed_paths
        if deleted:
            print(f"Removing {len(deleted)} deleted photos from index")
            placeholders = ",".join("?" * len(deleted))
            conn.execute(
                f"DELETE FROM history WHERE photo_id IN "
                f"(SELECT id FROM photos WHERE path IN ({placeholders}))",
                list(deleted),
            )
            conn.executemany("DELETE FROM photos WHERE path = ?", [(p,) for p in deleted])

    print(f"Indexed {len(indexed_paths)} photos")


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)
    index_photos(config)
    notify_app_reload(config)
