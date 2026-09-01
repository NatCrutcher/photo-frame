#!/usr/bin/env python3
"""List raw photos that have no corresponding exported JPEG.

Raw files aren't in frame.db (indexer.py only walks JPEGs), so this scans the
NAS filesystem directly. A raw is "covered" when a JPEG with the same filename
stem exists in the raw's own directory OR a sibling directory -- this is the
`DNG/` + `Original JPEG/` layout, where the raw and its export sit in adjacent
folders under a shared parent. Uncovered raws are the ones still needing an
Adobe Bridge export.

Output is one raw path per line on stdout, suitable as an input list for a
batch-conversion script. A per-format summary goes to stderr.

Usage:
    python3 find_unconverted_raws.py                 # scan mount from config.yaml
    python3 find_unconverted_raws.py config.yaml     # explicit config path
    python3 find_unconverted_raws.py > todo.txt      # capture the list to a file

Reuses nas.mount_point and the #recycle skip from indexer.py.
"""

import os
import sys

from indexer import EXCLUDED_DIRS, load_config

# Common raw formats across major manufacturers (Canon/Nikon/Sony/Fuji/
# Olympus/Panasonic/Pentax/Samsung/Sigma/Leica), plus Adobe DNG.
RAW_EXTENSIONS = {
    ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".srf",
    ".raf", ".orf", ".rw2", ".pef", ".srw", ".raw", ".rwl", ".dcr",
    ".kdc", ".mrw", ".x3f", ".3fr", ".mef", ".iiq", ".erf", ".mos",
}
JPEG_EXTENSIONS = (".jpg", ".jpeg")


def scan_tree(mount_point):
    """One filesystem walk. Return (raw_paths, jpeg_stems_by_dir).

    raw_paths: every raw file's absolute path.
    jpeg_stems_by_dir: directory -> set of lowercased JPEG filename stems.
    """
    raw_paths = []
    jpeg_stems_by_dir = {}
    for root, dirs, files in os.walk(mount_point):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            stem, ext = os.path.splitext(name)
            ext = ext.lower()
            if ext in RAW_EXTENSIONS:
                raw_paths.append(os.path.join(root, name))
            elif ext in JPEG_EXTENSIONS:
                jpeg_stems_by_dir.setdefault(root, set()).add(stem.lower())
    return raw_paths, jpeg_stems_by_dir


def jpeg_stems_by_parent(jpeg_stems_by_dir):
    """Roll each directory's JPEG stems up to its parent.

    Keying by the parent means a lookup for a raw covers every directory that
    shares that parent -- the raw's own folder and all its siblings at once.
    """
    by_parent = {}
    for directory, stems in jpeg_stems_by_dir.items():
        by_parent.setdefault(os.path.dirname(directory), set()).update(stems)
    return by_parent


def find_uncovered(raw_paths, stems_by_parent):
    """Return raw paths whose stem is absent from their own+sibling directories."""
    uncovered = []
    for path in raw_paths:
        parent = os.path.dirname(os.path.dirname(path))  # the raw's grandparent
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if stem not in stems_by_parent.get(parent, ()):
            uncovered.append(path)
    return uncovered


def print_summary(raw_paths, uncovered):
    """Per-format counts of uncovered raws to stderr, so stdout stays a clean list."""
    counts = {}
    for path in uncovered:
        ext = os.path.splitext(path)[1].lower()
        counts[ext] = counts.get(ext, 0) + 1
    print(f"{len(uncovered)} of {len(raw_paths)} raw files lack a JPEG:",
          file=sys.stderr)
    for ext in sorted(counts, key=lambda e: (-counts[e], e)):
        print(f"  {ext:<8} {counts[ext]:>7}", file=sys.stderr)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    mount_point = load_config(config_path)["nas"]["mount_point"]

    if not os.path.isdir(mount_point):
        print(f"Photo directory not found: {mount_point}", file=sys.stderr)
        sys.exit(1)

    raw_paths, jpeg_stems_by_dir = scan_tree(mount_point)
    uncovered = find_uncovered(raw_paths, jpeg_stems_by_parent(jpeg_stems_by_dir))

    for path in sorted(uncovered):
        print(path)
    print_summary(raw_paths, uncovered)


if __name__ == "__main__":
    main()
