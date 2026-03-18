#!/usr/bin/env python3
"""Scan a photo directory, extract metadata with exiftool, and populate SQLite."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import yaml

from db import get_db, init_db

BATCH_SIZE = 100


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def scan_photos(directory):
    """Return all JPEG file paths under directory."""
    paths = []
    for root, _dirs, files in os.walk(directory):
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

            conn.execute("""
                INSERT INTO photos (path, rating, keywords, people, width, height,
                                    orientation, file_modified_at, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    rating = excluded.rating,
                    keywords = excluded.keywords,
                    people = excluded.people,
                    width = excluded.width,
                    height = excluded.height,
                    orientation = excluded.orientation,
                    file_modified_at = excluded.file_modified_at,
                    indexed_at = excluded.indexed_at
            """, (
                os.path.abspath(path), rating,
                json.dumps(keywords), json.dumps(people),
                width, height, orientation, file_modified, now,
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
