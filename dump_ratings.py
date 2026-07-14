#!/usr/bin/env python3
"""Dump every photo's path and rating to CSV, distinguishing unrated from 0.

Adobe can't easily show the difference between an unrated image and one
explicitly rated 0 (or Lightroom's "Rejected" flag, stored as -1). This dumps
the raw values straight from frame.db so you can see them.

Usage:
    python3 dump_ratings.py                 # write CSV to stdout
    python3 dump_ratings.py ratings.csv     # write CSV to a file

Reads the same database as the app (frame.db, or $FRAME_DB).
"""

import csv
import sys

from db import get_db


def rating_label(rating):
    """Human-readable meaning for a raw rating value."""
    if rating is None:
        return "unrated"
    if rating == -1:
        return "rejected"
    if rating == 0:
        return "zero (explicit)"
    return f"{rating} star" + ("s" if rating != 1 else "")


def fetch_ratings():
    """Return (path, rating) rows, grouped by rating (NULL, -1, 0, 1..5), then path."""
    with get_db() as conn:
        return conn.execute(
            "SELECT path, rating FROM photos ORDER BY rating IS NOT NULL, rating, path"
        ).fetchall()


def write_csv(rows, out):
    writer = csv.writer(out)
    writer.writerow(["path", "rating", "rating_label"])
    for row in rows:
        rating = row["rating"]
        writer.writerow([row["path"], "" if rating is None else rating,
                         rating_label(rating)])


def print_summary(rows):
    """Print a per-rating count to stderr so it doesn't pollute the CSV."""
    counts = {}
    for row in rows:
        counts[row["rating"]] = counts.get(row["rating"], 0) + 1
    print(f"{len(rows)} photos:", file=sys.stderr)
    for rating in sorted(counts, key=lambda r: (r is not None, r)):
        print(f"  {rating_label(rating):<16} {counts[rating]:>7}", file=sys.stderr)


def main():
    rows = fetch_ratings()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", newline="") as f:
            write_csv(rows, f)
        print(f"Wrote {len(rows)} rows to {sys.argv[1]}", file=sys.stderr)
    else:
        write_csv(rows, sys.stdout)
    print_summary(rows)


if __name__ == "__main__":
    main()
