"""Synthetic Experiment 001-schema databases for Experiment 002 tests.

All titles, summaries and URLs used with this helper are made up.
"""

import json
from pathlib import Path

from atlas_signal import db

TS = "2026-01-10T12:00:00Z"

SOURCES = (
    ("pub-a", "Publisher A", "Publisher A", "publisher"),
    ("gov-b", "Government B", "Government B", "government"),
)


def article(id, source="pub-a", url=None, title="Made-up title", summary="Made-up summary.",
            date_status="ok", published_at=TS, updated_at=None, first_seen_at=TS,
            sightings=((None, None),)):
    """An article row plus its sightings as ``(sighting_id, categories)`` pairs.

    A ``None`` sighting id lets SQLite choose one.
    """
    return {
        "id": id, "source": source, "url": url or f"https://example.test/news/{id}",
        "title": title, "summary": summary, "date_status": date_status,
        "published_at": published_at, "updated_at": updated_at,
        "first_seen_at": first_seen_at, "sightings": sightings,
    }


def build_db(path: Path, articles) -> Path:
    conn = db.open_database(path)
    try:
        with conn:
            for sid, name, publisher, kind in SOURCES:
                conn.execute(
                    "INSERT INTO sources (id, name, publisher, kind, feed_url, first_added_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, name, publisher, kind, f"https://example.test/{sid}.xml", TS, TS))
            conn.execute(
                "INSERT INTO runs (id, started_at, finished_at, status, app_version)"
                " VALUES (1, ?, ?, 'completed', 'test')", (TS, TS))
            fetch_ids = {}
            for i, (sid, *_) in enumerate(SOURCES, start=1):
                conn.execute(
                    "INSERT INTO fetches (id, run_id, source_id, started_at, request_url, outcome)"
                    " VALUES (?, 1, ?, ?, ?, 'ok')", (i, sid, TS, f"https://example.test/{sid}.xml"))
                fetch_ids[sid] = i
            entry = 0
            for a in articles:
                conn.execute(
                    "INSERT INTO articles (id, normalized_url, first_source_id, title, summary,"
                    " published_at, updated_at, date_status, first_seen_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (a["id"], a["url"], a["source"], a["title"], a["summary"],
                     a["published_at"], a["updated_at"], a["date_status"],
                     a["first_seen_at"], a["first_seen_at"]))
                for sighting_id, categories in a["sightings"]:
                    entry += 1
                    conn.execute(
                        "INSERT INTO sightings (id, article_id, fetch_id, source_id, entry_index,"
                        " seen_at, match_method, url_as_seen, entry_sha256, raw_entry_json,"
                        " categories_json) VALUES (?, ?, ?, ?, ?, ?, 'new', ?, 'x', '{}', ?)",
                        (sighting_id, a["id"], fetch_ids[a["source"]], a["source"], entry, TS,
                         a["url"], None if categories is None else json.dumps(categories)))
    finally:
        conn.close()
    return path
