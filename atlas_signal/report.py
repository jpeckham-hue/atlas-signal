"""Read-only summary of collection activity for Experiment A.

The database is opened with SQLite's read-only mode and is never initialized,
migrated or synced. A missing file, an unsupported schema version or a missing
table/column is reported as an error rather than repaired.

With ``since`` (UTC midnight at the start of a date), each kind of activity is
filtered by its own timestamp: runs and fetches by ``started_at``, sightings by
``seen_at`` and new articles by ``first_seen_at``. Entry errors have no
timestamp and follow their fetch.
"""

import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from os import PathLike
from pathlib import Path

from atlas_signal.db import SCHEMA_SQL, SCHEMA_VERSION, TABLES, SchemaError, utc_timestamp

RUN_STATUSES = ("completed", "completed_with_errors", "failed", "running")
FETCH_OUTCOMES = ("ok", "not_modified", "http_error", "network_error",
                  "parse_error", "robots_disallowed", "too_large")
SUCCESS_OUTCOMES = ("ok", "not_modified")
MATCH_METHODS = ("new", "guid", "normalized_url")
DATE_STATUSES = ("ok", "updated_only", "unparseable", "missing", "future")
ARTICLE_FIELDS = ("title", "summary", "author", "language")

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class ReportError(Exception):
    """The database cannot be read for reporting."""


@dataclass(frozen=True)
class ChangeCount:
    """How many items had a predecessor to compare with, and how many differed."""

    compared: int = 0
    changed: int = 0


@dataclass(frozen=True)
class SourceReport:
    source_id: str
    fetches: int
    outcomes: dict[str, int]
    entries_observed: int
    entry_errors: int
    sightings: int
    match_methods: dict[str, int]
    guid_present: int              # of ``sightings``
    new_articles: int              # first seen from this source in the period
    fields_present: dict[str, int]  # of ``new_articles``, keyed by ARTICLE_FIELDS
    date_statuses: dict[str, int]  # of ``new_articles``
    fingerprint_changes: ChangeCount
    body_changes: ChangeCount

    @property
    def successful_fetches(self) -> int:
        return sum(self.outcomes.get(o, 0) for o in SUCCESS_OUTCOMES)


@dataclass(frozen=True)
class Report:
    generated_at: str
    since: str | None
    runs: dict[str, int]
    fetch_outcomes: dict[str, int]
    entries_observed: int
    entry_errors: int
    articles_all_time: int
    sightings: int
    new_articles: int
    match_methods: dict[str, int]
    cross_source_url_matches: int  # normalized_url matches to another source's article
    multi_source_articles: int
    multi_sighting_articles: int
    fingerprint_changes: ChangeCount
    fingerprint_changed_guids: int
    body_changes: ChangeCount
    sources: tuple[SourceReport, ...]

    @property
    def run_count(self) -> int:
        return sum(self.runs.values())

    @property
    def fetch_count(self) -> int:
        return sum(self.fetch_outcomes.values())


def parse_since(text: str) -> str:
    """``YYYY-MM-DD`` -> UTC midnight timestamp. Raises ValueError if malformed."""
    if not _DATE.fullmatch(text):
        raise ValueError(f"invalid --since date {text!r}; expected YYYY-MM-DD")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"invalid --since date {text!r}; not a real calendar date") from None
    return f"{day.isoformat()}T00:00:00Z"


def open_read_only(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open an existing Atlas Signal database read-only and check its schema.

    Raises ReportError if the file is missing or unreadable, SchemaError if it
    is not a version-1 Atlas Signal database. Never creates or changes a file.
    """
    file = Path(path)
    if not file.is_file():
        raise ReportError(f"database not found: {str(path)!r}")
    try:
        conn = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise ReportError(f"cannot open database {str(path)!r}: {err}") from None
    try:
        check_schema(conn)
    except sqlite3.Error as err:
        conn.close()
        raise ReportError(f"cannot read database {str(path)!r}: {err}") from None
    except BaseException:
        conn.close()
        raise
    return conn


def check_schema(conn: sqlite3.Connection) -> None:
    """Raise SchemaError unless ``conn`` has the version-1 tables and columns."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        if version == 0:
            raise SchemaError("database has no Atlas Signal schema version (is it empty"
                              " or not an Atlas Signal database?)")
        raise SchemaError(
            f"unsupported database schema version {version}; expected {SCHEMA_VERSION}")
    for table, expected in _expected_columns().items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not actual:
            raise SchemaError(f"database is missing table {table!r}")
        missing = [c for c in expected if c not in actual]
        if missing:
            raise SchemaError(f"table {table!r} is missing columns: {', '.join(missing)}")


def _expected_columns() -> dict[str, list[str]]:
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(SCHEMA_SQL)
        return {t: [row[1] for row in mem.execute(f"PRAGMA table_info({t})")] for t in TABLES}
    finally:
        mem.close()


def build_report(
    conn: sqlite3.Connection,
    since: str | None = None,
    *,
    now: Callable[[], str] = utc_timestamp,
) -> Report:
    """Summarize activity at or after ``since`` (a UTC timestamp), or all time."""
    s = since or ""  # every stored timestamp sorts after ""

    def counts(sql: str, *params) -> dict[str, int]:
        return {key: n for key, n in conn.execute(sql, params)}

    def scalar(sql: str, *params) -> int:
        return conn.execute(sql, params).fetchone()[0] or 0

    fingerprints, changed_guids = _fingerprint_changes(conn, s)
    bodies = _body_changes(conn, s)

    source_ids = sorted(
        row[0] for row in conn.execute(
            "SELECT source_id FROM fetches WHERE started_at >= ?"
            " UNION SELECT source_id FROM sightings WHERE seen_at >= ?"
            " UNION SELECT first_source_id FROM articles WHERE first_seen_at >= ?",
            (s, s, s),
        )
    )
    sources = tuple(
        _source_report(conn, sid, s, fingerprints.get(sid, ChangeCount()),
                       bodies.get(sid, ChangeCount()))
        for sid in source_ids
    )

    return Report(
        generated_at=now(),
        since=since,
        runs=counts("SELECT status, COUNT(*) FROM runs WHERE started_at >= ? GROUP BY 1", s),
        fetch_outcomes=counts(
            "SELECT outcome, COUNT(*) FROM fetches WHERE started_at >= ? GROUP BY 1", s),
        entries_observed=scalar("SELECT SUM(entry_count) FROM fetches WHERE started_at >= ?", s),
        entry_errors=scalar(
            "SELECT COUNT(*) FROM entry_errors e JOIN fetches f ON f.id = e.fetch_id"
            " WHERE f.started_at >= ?", s),
        articles_all_time=scalar("SELECT COUNT(*) FROM articles"),
        sightings=scalar("SELECT COUNT(*) FROM sightings WHERE seen_at >= ?", s),
        new_articles=scalar("SELECT COUNT(*) FROM articles WHERE first_seen_at >= ?", s),
        match_methods=counts(
            "SELECT match_method, COUNT(*) FROM sightings WHERE seen_at >= ? GROUP BY 1", s),
        cross_source_url_matches=scalar(
            "SELECT COUNT(*) FROM sightings g JOIN articles a ON a.id = g.article_id"
            " WHERE g.seen_at >= ? AND g.match_method = 'normalized_url'"
            " AND g.source_id != a.first_source_id", s),
        multi_source_articles=scalar(
            "SELECT COUNT(*) FROM (SELECT article_id FROM sightings WHERE seen_at >= ?"
            " GROUP BY article_id HAVING COUNT(DISTINCT source_id) > 1)", s),
        multi_sighting_articles=scalar(
            "SELECT COUNT(*) FROM (SELECT article_id FROM sightings WHERE seen_at >= ?"
            " GROUP BY article_id HAVING COUNT(*) > 1)", s),
        fingerprint_changes=_total(fingerprints.values()),
        fingerprint_changed_guids=changed_guids,
        body_changes=_total(bodies.values()),
        sources=sources,
    )


def _source_report(conn, source_id, s, fingerprint_changes, body_changes) -> SourceReport:
    def one(sql: str):
        return conn.execute(sql, (source_id, s)).fetchone()

    def counts(sql: str) -> dict[str, int]:
        return {key: n for key, n in conn.execute(sql, (source_id, s))}

    fetch_row = one(
        "SELECT COUNT(*), SUM(entry_count) FROM fetches WHERE source_id = ? AND started_at >= ?")
    sighting_row = one(
        "SELECT COUNT(*), SUM(entry_guid IS NOT NULL AND entry_guid != '') FROM sightings"
        " WHERE source_id = ? AND seen_at >= ?")
    present = ", ".join(f"SUM({f} IS NOT NULL AND {f} != '')" for f in ARTICLE_FIELDS)
    article_row = one(
        f"SELECT COUNT(*), {present} FROM articles WHERE first_source_id = ? AND first_seen_at >= ?")
    return SourceReport(
        source_id=source_id,
        fetches=fetch_row[0],
        outcomes=counts("SELECT outcome, COUNT(*) FROM fetches"
                        " WHERE source_id = ? AND started_at >= ? GROUP BY 1"),
        entries_observed=fetch_row[1] or 0,
        entry_errors=one(
            "SELECT COUNT(*) FROM entry_errors e JOIN fetches f ON f.id = e.fetch_id"
            " WHERE f.source_id = ? AND f.started_at >= ?")[0],
        sightings=sighting_row[0],
        match_methods=counts("SELECT match_method, COUNT(*) FROM sightings"
                             " WHERE source_id = ? AND seen_at >= ? GROUP BY 1"),
        guid_present=sighting_row[1] or 0,
        new_articles=article_row[0],
        fields_present={f: n or 0 for f, n in zip(ARTICLE_FIELDS, article_row[1:])},
        date_statuses=counts("SELECT date_status, COUNT(*) FROM articles"
                             " WHERE first_source_id = ? AND first_seen_at >= ? GROUP BY 1"),
        fingerprint_changes=fingerprint_changes,
        body_changes=body_changes,
    )


def _fingerprint_changes(conn, s) -> tuple[dict[str, ChangeCount], int]:
    """Per source: sightings in the period whose same-source GUID was seen in an
    earlier fetch, and how many of those have a different ``entry_sha256`` from
    that earlier fetch. Also returns the number of distinct changed GUIDs."""
    compared: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    changed_keys = set()
    key = current_fetch = current_sha = previous_sha = None
    for source_id, guid, fetch_id, sha, seen_at in conn.execute(
        "SELECT source_id, entry_guid, fetch_id, entry_sha256, seen_at FROM sightings"
        " WHERE entry_guid IS NOT NULL AND entry_guid != ''"
        " ORDER BY source_id, entry_guid, fetch_id, entry_index"
    ):
        if (source_id, guid) != key:
            key, current_fetch, current_sha, previous_sha = (source_id, guid), None, None, None
        if fetch_id != current_fetch:
            previous_sha = current_sha  # last fingerprint from the latest earlier fetch
            current_fetch = fetch_id
        current_sha = sha
        if seen_at >= s and previous_sha is not None:
            compared[source_id] += 1
            if sha != previous_sha:
                changed[source_id] += 1
                changed_keys.add(key)
    result = {sid: ChangeCount(compared[sid], changed[sid]) for sid in compared}
    return result, len(changed_keys)


def _body_changes(conn, s) -> dict[str, ChangeCount]:
    """Per source: successful fetches in the period that have a preceding
    successful fetch with a body, and how many have a different body hash."""
    compared: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    previous: dict[str, str] = {}
    for source_id, sha, started_at in conn.execute(
        "SELECT source_id, body_sha256, started_at FROM fetches"
        " WHERE outcome = 'ok' AND body_sha256 IS NOT NULL ORDER BY id"
    ):
        if started_at >= s and source_id in previous:
            compared[source_id] += 1
            if sha != previous[source_id]:
                changed[source_id] += 1
        previous[source_id] = sha
    return {sid: ChangeCount(compared[sid], changed[sid]) for sid in compared}


def _total(changes) -> ChangeCount:
    changes = list(changes)
    return ChangeCount(sum(c.compared for c in changes), sum(c.changed for c in changes))
