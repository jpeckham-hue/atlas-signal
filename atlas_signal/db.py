"""SQLite connection setup and schema for Experiment A (feed collection).

Only low-level primitives live here: opening a configured connection and
creating or recognising the schema. Timestamps are stored as UTC text in the
form ``YYYY-MM-DDTHH:MM:SSZ``; the schema enforces that shape.
"""

import sqlite3
from datetime import datetime, timezone
from os import PathLike

SCHEMA_VERSION = 1

TABLES = ("sources", "runs", "fetches", "articles", "sightings", "entry_errors")

# GLOB pattern for the agreed timestamp format, e.g. 2026-10-04T17:30:00Z.
_TS = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'"


def _ts(column: str, nullable: bool = False) -> str:
    check = f"{column} GLOB {_TS}"
    if nullable:
        check = f"{column} IS NULL OR {check}"
    return f"CHECK ({check})"


SCHEMA_SQL = f"""
CREATE TABLE sources (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  publisher      TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('publisher', 'government')),
  feed_url       TEXT NOT NULL,
  enabled        INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  first_added_at TEXT NOT NULL {_ts('first_added_at')},
  updated_at     TEXT NOT NULL {_ts('updated_at')}
) STRICT;

CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  started_at    TEXT NOT NULL {_ts('started_at')},
  finished_at   TEXT {_ts('finished_at', nullable=True)},
  status        TEXT NOT NULL CHECK (status IN
                  ('running', 'completed', 'completed_with_errors', 'failed')),
  app_version   TEXT NOT NULL,
  error_message TEXT
) STRICT;

CREATE TABLE fetches (
  id                INTEGER PRIMARY KEY,
  run_id            INTEGER NOT NULL REFERENCES runs(id),
  source_id         TEXT NOT NULL REFERENCES sources(id),
  started_at        TEXT NOT NULL {_ts('started_at')},
  finished_at       TEXT {_ts('finished_at', nullable=True)},
  request_url       TEXT NOT NULL,
  final_url         TEXT,
  http_status       INTEGER,
  outcome           TEXT NOT NULL CHECK (outcome IN
                      ('ok', 'not_modified', 'http_error', 'network_error',
                       'parse_error', 'robots_disallowed', 'too_large')),
  error_message     TEXT,
  etag              TEXT,
  last_modified     TEXT,
  body_sha256       TEXT,
  body_bytes        INTEGER,
  bozo              INTEGER NOT NULL DEFAULT 0 CHECK (bozo IN (0, 1)),
  bozo_message      TEXT,
  entry_count       INTEGER,
  new_article_count INTEGER,
  entry_error_count INTEGER
) STRICT;

CREATE TABLE articles (
  id              INTEGER PRIMARY KEY,
  normalized_url  TEXT NOT NULL UNIQUE,
  canonical_url   TEXT,
  first_source_id TEXT NOT NULL REFERENCES sources(id),
  title           TEXT,
  summary         TEXT,
  author          TEXT,
  language        TEXT,
  published_at    TEXT {_ts('published_at', nullable=True)},
  updated_at      TEXT {_ts('updated_at', nullable=True)},
  date_status     TEXT NOT NULL CHECK (date_status IN
                    ('ok', 'updated_only', 'unparseable', 'missing', 'future')),
  first_seen_at   TEXT NOT NULL {_ts('first_seen_at')},
  last_seen_at    TEXT NOT NULL {_ts('last_seen_at')}
) STRICT;

CREATE TABLE sightings (
  id              INTEGER PRIMARY KEY,
  article_id      INTEGER NOT NULL REFERENCES articles(id),
  fetch_id        INTEGER NOT NULL REFERENCES fetches(id),
  source_id       TEXT NOT NULL REFERENCES sources(id),
  entry_index     INTEGER NOT NULL,
  seen_at         TEXT NOT NULL {_ts('seen_at')},
  match_method    TEXT NOT NULL CHECK (match_method IN
                    ('new', 'guid', 'normalized_url')),
  entry_guid      TEXT,
  url_as_seen     TEXT NOT NULL,
  title_raw       TEXT,
  summary_raw     TEXT,
  author_raw      TEXT,
  published_raw   TEXT,
  updated_raw     TEXT,
  categories_json TEXT,
  entry_sha256    TEXT NOT NULL,
  raw_entry_json  TEXT NOT NULL,
  UNIQUE (fetch_id, entry_index)
) STRICT;

CREATE INDEX sightings_source_guid ON sightings(source_id, entry_guid);
CREATE INDEX sightings_article ON sightings(article_id);

CREATE TABLE entry_errors (
  id             INTEGER PRIMARY KEY,
  fetch_id       INTEGER NOT NULL REFERENCES fetches(id),
  entry_index    INTEGER NOT NULL,
  entry_guid     TEXT,
  link_raw       TEXT,
  error          TEXT NOT NULL,
  raw_entry_json TEXT
) STRICT;
"""


class SchemaError(Exception):
    """The database is not one this version of Atlas Signal can use."""


def utc_timestamp(moment: datetime | None = None) -> str:
    """Format a moment (default: now) as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC.

    Fractional seconds are dropped. Raises ValueError for a naive datetime,
    since its time zone would be a guess.
    """
    if moment is None:
        moment = datetime.now(timezone.utc)
    elif moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key enforcement switched on.

    Raises SchemaError if SQLite refuses to enable foreign keys.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SchemaError("SQLite foreign-key enforcement could not be enabled")
    except BaseException:
        conn.close()
        raise
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create the version-1 schema in an empty database, or accept an existing one.

    - Empty database (user_version 0, no tables): the schema is created and
      user_version set to 1 in a single transaction.
    - user_version 1 with all expected tables: left unchanged.
    - Anything else (another version, or user_version 0 with tables already
      present): SchemaError is raised and the database is not modified.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }

    if version == SCHEMA_VERSION:
        missing = set(TABLES) - existing
        if missing:
            raise SchemaError(
                f"database claims schema version {version} but is missing tables: "
                + ", ".join(sorted(missing))
            )
        return

    if version != 0:
        raise SchemaError(
            f"unsupported database schema version {version}; expected {SCHEMA_VERSION}"
        )
    if existing:
        raise SchemaError(
            "database has no schema version but already contains tables: "
            + ", ".join(sorted(existing))
        )

    try:
        conn.executescript(
            f"BEGIN;\n{SCHEMA_SQL}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def open_database(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open the database at ``path`` and ensure it has the version-1 schema."""
    conn = connect(path)
    try:
        initialize(conn)
    except BaseException:
        conn.close()
        raise
    return conn
