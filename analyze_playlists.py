#!/usr/bin/env python3
"""Inspect what each playlist actually resolves to, straight from frame.db.

Two modes:
  * counts (default) -- how many photos each playlist contains, so you can spot
    a filter that unexpectedly shrinks the pool.
  * dump -- every photo in one playlist as CSV (path, rating, date, keywords).

It builds each playlist the exact same way the running app does -- same filter,
same global excludes, via playlist.get_playlist_photos -- so the numbers match
what the frame plays. Shuffle is forced off for stable, comparable output.

Usage:
    python3 analyze_playlists.py                      # counts, config.yaml
    python3 analyze_playlists.py config.yaml          # counts, explicit config
    python3 analyze_playlists.py --dump gte2019       # CSV of one playlist to stdout
    python3 analyze_playlists.py --dump gte2019 > out.csv

Reads the same database as the app (frame.db, or $FRAME_DB).
"""

import argparse
import csv
import sys

from indexer import load_config
from playlist import get_playlist_photos, load_playlists, validate_playlists


def materialize(config, playlists, playlist_id):
    """Resolve one playlist to its photo list, mirroring FrameState.load_playlist."""
    photos = get_playlist_photos(
        playlists[playlist_id],
        shuffle=False,
        global_exclude=config.get("exclude"),
        mount_point=config["nas"]["mount_point"],
    )
    # row_to_dict returns None only for a None row, which a SELECT never yields;
    # drop any anyway so downstream rows are always real photo dicts.
    return [photo for photo in photos if photo is not None]


def print_counts(config, playlists):
    """Print a per-playlist photo count table to stdout."""
    rows = [
        (pid, pc.get("name", pid), len(materialize(config, playlists, pid)))
        for pid, pc in playlists.items()
    ]
    id_width = max((len(pid) for pid, _, _ in rows), default=2)
    name_width = max((len(name) for _, name, _ in rows), default=4)
    for pid, name, count in rows:
        print(f"{pid:<{id_width}}  {name:<{name_width}}  {count:>7}")
    total = sum(count for _, _, count in rows)
    print(f"{len(rows)} playlists, {total} photos total (with overlap)",
          file=sys.stderr)


def dump_playlist(config, playlists, playlist_id):
    """Write one playlist's photos as CSV to stdout; count to stderr."""
    photos = materialize(config, playlists, playlist_id)
    writer = csv.writer(sys.stdout)
    writer.writerow(["path", "rating", "date_taken", "keywords"])
    for photo in photos:
        writer.writerow([
            photo["path"],
            "" if photo["rating"] is None else photo["rating"],
            photo["date_taken"] or "",
            "; ".join(photo.get("keywords") or []),
        ])
    print(f"{len(photos)} photos in playlist '{playlist_id}'", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Analyze photo-frame playlists.")
    parser.add_argument("config", nargs="?", default="config.yaml",
                        help="path to config.yaml (default: config.yaml)")
    parser.add_argument("--dump", metavar="PLAYLIST_ID",
                        help="dump this playlist's photos as CSV instead of counts")
    args = parser.parse_args()

    config = load_config(args.config)
    playlists = load_playlists(config)
    validate_playlists(playlists)  # fail fast on a misplaced filter, like the app

    if args.dump:
        if args.dump not in playlists:
            valid = ", ".join(playlists) or "(none)"
            print(f"Unknown playlist '{args.dump}'. Valid: {valid}", file=sys.stderr)
            sys.exit(1)
        dump_playlist(config, playlists, args.dump)
    else:
        print_counts(config, playlists)


if __name__ == "__main__":
    main()
