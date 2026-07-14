"""Playlist filtering engine — applies config-defined filters to the photo database."""

import datetime
import os
import random

from db import get_db, row_to_dict


def load_playlists(config):
    """Return dict of playlist_id -> playlist config."""
    return config.get("playlists", {})


def _escape_like(s):
    """Escape LIKE wildcards so path literals can't act as patterns (ESCAPE '\\')."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _period_start(value):
    """Start of an ISO date shortcut: 2023 -> 2023-01-01, 2023-08 -> 2023-08-01."""
    parts = str(value).strip()[:10].split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    day = int(parts[2]) if len(parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _next_period(value):
    """Start of the period *after* an ISO date shortcut, for inclusive upper bounds.

    2023 -> 2024-01-01, 2023-08 -> 2023-09-01, 2023-08-15 -> 2023-08-16.
    """
    parts = str(value).strip()[:10].split("-")
    year = int(parts[0])
    if len(parts) == 1:                      # year only -> next year
        return f"{year + 1:04d}-01-01"
    month = int(parts[1])
    if len(parts) == 2:                      # year-month -> next month (roll year)
        return f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
    day = int(parts[2])                      # full date -> next day
    nxt = datetime.date(year, month, day) + datetime.timedelta(days=1)
    return nxt.isoformat()


def build_query(playlist_filter, mount_point=None):
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

    if "date_taken" in playlist_filter:
        df = playlist_filter["date_taken"]
        gte, lte = df.get("gte"), df.get("lte")
        if "between" in df:
            gte, lte = df["between"]
        if gte is not None:
            conditions.append("date_taken >= ?")
            params.append(_period_start(gte))
        if lte is not None:
            # Upper bound is inclusive of the whole period; compare against the
            # start of the next period so the stored HH:MM:SS never drops matches.
            conditions.append("date_taken < ?")
            params.append(_next_period(lte))

    if "folders" in playlist_filter:
        root = os.path.abspath(mount_point) if mount_point else ""
        clauses = []
        for folder in playlist_filter["folders"]:
            prefix = os.path.join(root, folder.strip("/"))
            clauses.append("path LIKE ? ESCAPE '\\'")
            params.append(_escape_like(prefix) + "/%")
        if clauses:
            conditions.append(f"({' OR '.join(clauses)})")

    if "orientation" in playlist_filter:
        conditions.append("orientation = ?")
        params.append(playlist_filter["orientation"])

    where = " AND ".join(conditions) if conditions else "1=1"
    return where, params


def build_exclude_conditions(exclude_config):
    """Build SQL conditions that exclude matching photos."""
    conditions = []
    params = []
    for person in exclude_config.get("people", []):
        conditions.append("NOT (people LIKE ?)")
        params.append(f'%"{person}"%')
    for kw in exclude_config.get("keywords", []):
        conditions.append("NOT (keywords LIKE ?)")
        params.append(f'%"{kw}"%')
    ratings = exclude_config.get("ratings", [])
    if ratings:
        # Drop the listed ratings but keep unrated photos (rating IS NULL);
        # a plain NOT IN would also discard NULLs.
        placeholders = ", ".join("?" for _ in ratings)
        conditions.append(f"(rating IS NULL OR rating NOT IN ({placeholders}))")
        params.extend(ratings)
    return conditions, params


def get_playlist_photos(playlist_config, shuffle=True, global_exclude=None,
                        mount_point=None):
    """Return list of photo dicts matching a playlist's filter."""
    filt = playlist_config.get("filter", {})
    where, params = build_query(filt, mount_point)

    # Playlist-level excludes
    playlist_exclude = playlist_config.get("exclude", {})
    if playlist_exclude:
        conds, prms = build_exclude_conditions(playlist_exclude)
        if conds:
            where += " AND " + " AND ".join(conds)
            params.extend(prms)

    # Global excludes
    if global_exclude:
        conds, prms = build_exclude_conditions(global_exclude)
        if conds:
            where += " AND " + " AND ".join(conds)
            params.extend(prms)

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM photos WHERE {where}", params
        ).fetchall()

    photos = [row_to_dict(r) for r in rows]
    if shuffle:
        random.shuffle(photos)
    return photos
