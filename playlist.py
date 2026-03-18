"""Playlist filtering engine — applies config-defined filters to the photo database."""

import random

from db import get_db, row_to_dict


def load_playlists(config):
    """Return dict of playlist_id -> playlist config."""
    return config.get("playlists", {})


def build_query(playlist_filter):
    """Build a SQL WHERE clause from a playlist filter definition."""
    conditions = []
    params = []

    if "rating" in playlist_filter:
        rf = playlist_filter["rating"]
        if "gte" in rf:
            conditions.append("rating >= ?")
            params.append(rf["gte"])
        if "lte" in rf:
            conditions.append("rating <= ?")
            params.append(rf["lte"])
        if "eq" in rf:
            conditions.append("rating = ?")
            params.append(rf["eq"])

    if "people" in playlist_filter:
        pf = playlist_filter["people"]
        if "any" in pf:
            clauses = []
            for person in pf["any"]:
                clauses.append("people LIKE ?")
                params.append(f'%"{person}"%')
            conditions.append(f"({' OR '.join(clauses)})")
        if "all" in pf:
            for person in pf["all"]:
                conditions.append("people LIKE ?")
                params.append(f'%"{person}"%')

    if "keywords" in playlist_filter:
        kf = playlist_filter["keywords"]
        if "any" in kf:
            clauses = []
            for kw in kf["any"]:
                clauses.append("keywords LIKE ?")
                params.append(f'%"{kw}"%')
            conditions.append(f"({' OR '.join(clauses)})")
        if "all" in kf:
            for kw in kf["all"]:
                conditions.append("keywords LIKE ?")
                params.append(f'%"{kw}"%')

    if "orientation" in playlist_filter:
        conditions.append("orientation = ?")
        params.append(playlist_filter["orientation"])

    where = " AND ".join(conditions) if conditions else "1=1"
    return where, params


def get_playlist_photos(playlist_config, shuffle=True):
    """Return list of photo dicts matching a playlist's filter."""
    filt = playlist_config.get("filter", {})
    where, params = build_query(filt)

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM photos WHERE {where}", params
        ).fetchall()

    photos = [row_to_dict(r) for r in rows]
    if shuffle:
        random.shuffle(photos)
    return photos
