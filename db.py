"""SQLite database for the photo frame."""

import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("FRAME_DB", "frame.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                rating INTEGER,
                keywords TEXT,
                people TEXT,
                width INTEGER,
                height INTEGER,
                orientation TEXT,
                file_modified_at TEXT,
                indexed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY,
                photo_id INTEGER REFERENCES photos(id),
                playlist TEXT,
                played_at TEXT,
                duration_shown REAL
            );

            CREATE INDEX IF NOT EXISTS idx_photos_path ON photos(path);
            CREATE INDEX IF NOT EXISTS idx_photos_rating ON photos(rating);
            CREATE INDEX IF NOT EXISTS idx_history_played_at ON history(played_at);
        """)


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("keywords", "people"):
        if key in d and d[key]:
            d[key] = json.loads(d[key])
        elif key in d:
            d[key] = []
    return d
